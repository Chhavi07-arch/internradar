"""Funding radar: read funding news → record rounds → find the funded company's careers board.

A company that just raised usually starts hiring within weeks. Once its board is found, the
regular fetch polls it like any watchlisted company, so its internships show up the hour they
are posted.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlmodel import Session, col, select

from internradar.config import WatchedCompany, Watchlist
from internradar.funding.boards import find_board
from internradar.funding.feeds import FEEDS, USER_AGENT, FeedItem, fetch_feed
from internradar.funding.headlines import parse_headline
from internradar.models import FundingEvent, FundingMention, utcnow
from internradar.normalize import normalize_company

SAME_ROUND_WINDOW = timedelta(days=21)  # reports of one round across outlets and days
SIGNAL_HORIZON = timedelta(days=180)  # how long a round counts as a hiring signal
BOARD_RECHECK = timedelta(days=7)  # funded startups often open a board weeks later
MAX_PARALLEL = 6


@dataclass
class RadarSummary:
    feeds: dict[str, int | str] = field(default_factory=dict)  # items read, or the error
    new_events: list[FundingEvent] = field(default_factory=list)
    boards_found: list[FundingEvent] = field(default_factory=list)


def make_client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def run_radar(session: Session, watchlist: Watchlist, client: httpx.Client) -> RadarSummary:
    summary = RadarSummary()
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        futures = {name: pool.submit(fetch_feed, client, name, url) for name, url in FEEDS.items()}
    items: list[FeedItem] = []
    for name, future in futures.items():
        try:
            items += future.result()
            summary.feeds[name] = len(future.result())
        except (httpx.HTTPError, ValueError) as exc:  # ElementTree.ParseError is a ValueError
            summary.feeds[name] = f"{type(exc).__name__}: {exc}"

    for item in sorted(items, key=lambda i: i.published_at or utcnow()):
        if event := _record(session, item):
            summary.new_events.append(event)

    summary.boards_found = _discover_boards(session, watchlist, client)
    return summary


def _record(session: Session, item: FeedItem) -> FundingEvent | None:
    """Store one article. Returns the event only if it is a round we had not seen."""
    if session.exec(select(FundingMention.id).where(FundingMention.url == item.url)).first():
        return None
    deal = parse_headline(item.title)
    if deal is None:
        return None

    published = item.published_at or utcnow()
    company_norm = normalize_company(deal.company)
    event = session.exec(
        select(FundingEvent).where(
            FundingEvent.company_norm == company_norm,
            FundingEvent.published_at >= published - SAME_ROUND_WINDOW,
            FundingEvent.published_at <= published + SAME_ROUND_WINDOW,
        )
    ).first()
    created = event is None
    if event is None:
        event = FundingEvent(
            company=deal.company,
            company_norm=company_norm,
            headline=item.title,
            url=item.url,
            published_at=published,
        )
        session.add(event)
    # Later reports often add what the first one lacked (round name, lead investor).
    for name in ("round", "amount_text", "amount_usd", "lead_investors"):
        if getattr(event, name) is None:
            setattr(event, name, getattr(deal, name))
    event.is_tech = event.is_tech or deal.is_tech
    session.flush()
    session.add(
        FundingMention(event_id=event.id, url=item.url, feed=item.feed, headline=item.title)
    )
    session.flush()
    return event if created else None


def _discover_boards(
    session: Session, watchlist: Watchlist, client: httpx.Client
) -> list[FundingEvent]:
    now = utcnow()
    watched = {normalize_company(c.name) for c in watchlist.companies}
    due = session.exec(
        select(FundingEvent).where(
            col(FundingEvent.is_tech).is_(True),
            col(FundingEvent.board_slug).is_(None),
            FundingEvent.published_at >= now - SIGNAL_HORIZON,
        )
    ).all()
    due = [
        e
        for e in due
        if e.company_norm not in watched
        and (e.board_checked_at is None or e.board_checked_at < now - BOARD_RECHECK)
    ]
    by_company = {e.company_norm: e.company for e in due}

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        matches = dict(
            zip(
                by_company,
                pool.map(lambda c: find_board(client, c), by_company.values()),
                strict=True,
            )
        )

    found = []
    for event in due:
        event.board_checked_at = now
        if match := matches.get(event.company_norm):
            event.board_ats, event.board_slug = match.ats, match.slug
            found.append(event)
    session.flush()
    return found


def funded_companies(session: Session, watchlist: Watchlist) -> list[WatchedCompany]:
    """Boards discovered by the radar, to poll alongside the hand-written watchlist."""
    watched = {(c.ats, c.slug) for c in watchlist.companies}
    events = session.exec(
        select(FundingEvent)
        .where(
            col(FundingEvent.board_slug).is_not(None),
            FundingEvent.published_at >= utcnow() - SIGNAL_HORIZON,
        )
        .order_by(col(FundingEvent.published_at).desc())
    ).all()
    companies: dict[tuple[str, str], WatchedCompany] = {}
    for e in events:
        key = (e.board_ats or "", e.board_slug or "")
        if key not in watched and key not in companies:
            companies[key] = WatchedCompany(
                name=e.company,
                ats=e.board_ats,  # type: ignore[arg-type]  (validated by find_board)
                slug=e.board_slug or "",
                tags=["funding-radar"],
                funding=_describe(e),
            )
    return list(companies.values())


def _describe(event: FundingEvent) -> str:
    parts = [event.round, event.amount_text, _month(event.published_at)]
    return " ".join(p for p in parts if p)


def _month(moment: datetime) -> str:
    return f"({moment:%b %Y})"
