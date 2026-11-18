"""One fetch run: fetch every source → keep relevant postings → de-duplicate → store → expire.

De-duplication tiers, cheapest and most certain first:
  1. same (source, native_id)       → the very same listing seen again
  2. same canonical URL             → another source linking to the same posting
  3. same fingerprint               → company + title + city normalise to the same thing
  4. same company and city, titles ≥ FUZZY_TITLE_THRESHOLD similar
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import update
from sqlmodel import Session, select

from internradar.config import SearchConfig
from internradar.models import (
    Kind,
    Lifecycle,
    Opportunity,
    Origin,
    Run,
    Sighting,
    SourceRun,
    utcnow,
)
from internradar.normalize import (
    canonical_url,
    fingerprint,
    normalize_city,
    normalize_company,
    normalize_title,
)
from internradar.relevance import is_internship, is_relevant
from internradar.sources import Posting, Source

FUZZY_TITLE_THRESHOLD = 90
MISSES_BEFORE_CLOSE = 2  # one flaky fetch must not close a live posting
BOARD_LISTING_TTL = timedelta(days=21)  # boards only show a recent window, so expire by age
MAX_PARALLEL_SOURCES = 8

# Fields a fetch may write. User state (bookmarked, hidden, notes) is deliberately absent.
_SOURCE_FIELDS = (
    "title", "company", "location", "url", "apply_url", "description", "posted_at",
    "deadline_at", "stipend_min", "stipend_max", "currency", "pay_period",
)  # fmt: skip


@dataclass
class RunSummary:
    run_id: int
    sources: list[SourceRun]

    @property
    def new(self) -> int:
        return sum(s.new for s in self.sources)


def run_fetch(
    session: Session, sources: list[Source], search: SearchConfig, trigger: str = "cli"
) -> RunSummary:
    now = utcnow()
    run = Run(started_at=now, trigger=trigger)
    session.add(run)
    session.flush()
    # "new" means new in the latest run, so last run's arrivals become ordinary.
    session.execute(
        update(Opportunity)
        .where(Opportunity.status == Lifecycle.NEW)
        .values(status=Lifecycle.ACTIVE)
        .execution_options(synchronize_session="fetch")
    )

    workers = max(1, min(MAX_PARALLEL_SOURCES, len(sources)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda source: source.fetch(search), sources))

    source_runs = []
    for result in results:
        kept = [p for p in result.postings if is_relevant(p, search)]
        new = sum(_ingest(session, p, now) for p in kept)
        if result.ok and result.exhaustive:
            _expire_missing(session, result.source, {p.native_id for p in result.postings}, now)
        source_run = SourceRun(
            run_id=run.id,
            source=result.source,
            ok=result.ok,
            exhaustive=result.exhaustive,
            fetched=len(result.postings),
            kept=len(kept),
            new=new,
            duration_ms=result.duration_ms,
            error=result.error,
        )
        session.add(source_run)
        source_runs.append(source_run)

    _expire_stale_board_listings(session, now)
    _close_opportunities_without_open_sightings(session)
    run.finished_at = utcnow()
    session.flush()
    return RunSummary(run_id=run.id, sources=source_runs)


def _ingest(session: Session, posting: Posting, now: datetime) -> bool:
    """Store one posting. Returns True if it created a new opportunity."""
    url = canonical_url(posting.url)
    sighting = session.exec(
        select(Sighting).where(
            Sighting.source == posting.source, Sighting.native_id == posting.native_id
        )
    ).first()
    if sighting:
        sighting.last_seen_at = now
        sighting.missed_runs = 0
        sighting.closed_at = None
        sighting.url = url or sighting.url
        _merge(session.get_one(Opportunity, sighting.opportunity_id), posting, url, now)
        return False

    opportunity = _find_match(session, posting, url)
    created = opportunity is None
    if opportunity is None:
        opportunity = _new_opportunity(posting, url, now)
        session.add(opportunity)
        session.flush()
    else:
        _merge(opportunity, posting, url, now)

    session.add(
        Sighting(
            opportunity_id=opportunity.id,
            source=posting.source,
            origin=posting.origin,
            native_id=posting.native_id,
            url=url,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    session.flush()
    return created


def _find_match(session: Session, posting: Posting, url: str | None) -> Opportunity | None:
    if url:
        opportunity_id = session.exec(
            select(Sighting.opportunity_id).where(Sighting.url == url)
        ).first()
        if opportunity_id is not None:
            return session.get(Opportunity, opportunity_id)

    uid = fingerprint(posting.company, posting.title, posting.location)
    if match := session.exec(select(Opportunity).where(Opportunity.uid == uid)).first():
        return match

    city = normalize_city(posting.location)
    title = normalize_title(posting.title)
    candidates = [
        o
        for o in session.exec(
            select(Opportunity).where(
                Opportunity.company_norm == normalize_company(posting.company)
            )
        )
        if o.city == city
    ]
    best = max(candidates, key=lambda o: fuzz.token_sort_ratio(o.title_norm, title), default=None)
    if best and fuzz.token_sort_ratio(best.title_norm, title) >= FUZZY_TITLE_THRESHOLD:
        return best
    return None


def _new_opportunity(posting: Posting, url: str | None, now: datetime) -> Opportunity:
    city = normalize_city(posting.location)
    return Opportunity(
        uid=fingerprint(posting.company, posting.title, posting.location),
        kind=Kind.INTERNSHIP
        if is_internship(posting.title, posting.flagged_internship)
        else Kind.JOB,
        company_norm=normalize_company(posting.company),
        title_norm=normalize_title(posting.title),
        city=city,
        is_remote=posting.is_remote or city == "remote",
        primary_origin=posting.origin,
        status=Lifecycle.NEW,
        first_seen_at=now,
        last_seen_at=now,
        **{field: getattr(posting, field) for field in _SOURCE_FIELDS if field != "url"},
        url=url,
    )


def _merge(opportunity: Opportunity, posting: Posting, url: str | None, now: datetime) -> None:
    """Fold a new sighting into an opportunity.

    A more authoritative origin (company ATS beats a board, which beats an email) overwrites
    the descriptive fields; an equal or weaker one only fills fields that are still empty.
    """
    opportunity.last_seen_at = now
    if opportunity.status == Lifecycle.CLOSED:
        opportunity.status = Lifecycle.ACTIVE

    better = posting.origin.rank < Origin(opportunity.primary_origin).rank
    incoming = {field: getattr(posting, field) for field in _SOURCE_FIELDS} | {"url": url}
    for field, value in incoming.items():
        if value is not None and (better or getattr(opportunity, field) is None):
            setattr(opportunity, field, value)

    if better:
        opportunity.primary_origin = posting.origin
        opportunity.company_norm = normalize_company(opportunity.company)
        opportunity.title_norm = normalize_title(opportunity.title)
        opportunity.city = normalize_city(opportunity.location)
        opportunity.is_remote = posting.is_remote or opportunity.city == "remote"


def _expire_missing(session: Session, source: str, seen: set[str], now: datetime) -> None:
    """For exhaustive sources: a listing absent from consecutive successful runs is closed."""
    open_sightings = session.exec(
        select(Sighting).where(Sighting.source == source, Sighting.closed_at.is_(None))
    )
    for sighting in open_sightings:
        if sighting.native_id in seen:
            continue
        sighting.missed_runs += 1
        if sighting.missed_runs >= MISSES_BEFORE_CLOSE:
            sighting.closed_at = now


def _expire_stale_board_listings(session: Session, now: datetime) -> None:
    session.execute(
        update(Sighting)
        .where(
            Sighting.origin == Origin.BOARD,
            Sighting.closed_at.is_(None),
            Sighting.last_seen_at < now - BOARD_LISTING_TTL,
        )
        .values(closed_at=now)
        .execution_options(synchronize_session="fetch")
    )


def _close_opportunities_without_open_sightings(session: Session) -> None:
    session.flush()
    has_open_sighting = (
        select(Sighting.id)
        .where(Sighting.opportunity_id == Opportunity.id, Sighting.closed_at.is_(None))
        .exists()
    )
    session.execute(
        update(Opportunity)
        .where(Opportunity.status != Lifecycle.CLOSED, ~has_open_sighting)
        .values(status=Lifecycle.CLOSED)
        .execution_options(synchronize_session="fetch")
    )
