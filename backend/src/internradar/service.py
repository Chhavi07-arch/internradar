"""One full refresh (inboxes → every source → store), shared by the CLI and the web API."""

import threading
from dataclasses import dataclass, field

import httpx

from internradar.config import (
    Watchlist,
    get_settings,
    load_gmail_config,
    load_search_config,
    load_watchlist,
)
from internradar.db import init_db, session_scope
from internradar.funding.radar import funded_companies
from internradar.gmail import auth
from internradar.gmail.client import GmailClient
from internradar.gmail.extract import build_extractor
from internradar.gmail.source import build_email_sources
from internradar.gmail.sync import SyncResult, sync_account
from internradar.models import SourceRun
from internradar.pipeline import run_fetch
from internradar.sources import build_sources

# Two overlapping fetches would fight over the same rows; the API and CLI share this lock.
FETCH_LOCK = threading.Lock()


class NoSourcesError(ValueError):
    pass


@dataclass
class FetchReport:
    run_id: int
    sources: list[SourceRun]
    inboxes: list[SyncResult] = field(default_factory=list)
    radar_boards: int = 0

    @property
    def new(self) -> int:
        return sum(s.new for s in self.sources)


def sync_inboxes() -> list[SyncResult]:
    settings = get_settings()
    config = load_gmail_config()
    extractor = build_extractor(settings)
    results = []
    for account in config.accounts:
        if not auth.is_connected(account.label):
            continue
        try:
            gmail = GmailClient(auth.load(account.label).token)
            with session_scope() as session:
                results.append(
                    sync_account(session, account, gmail, extractor, config.lookback_days)
                )
        except (auth.NotConnectedError, httpx.HTTPError) as exc:
            results.append(SyncResult(account=account.label, errors=[str(exc)]))
    return results


def fetch_everything(
    only: set[str] | None = None, gmail: bool = True, trigger: str = "cli"
) -> FetchReport:
    """Sync inboxes, then fetch every matching source once. Raises if a fetch is running."""
    if not FETCH_LOCK.acquire(blocking=False):
        raise RuntimeError("a fetch is already running")
    try:
        init_db()
        search = load_search_config()
        watchlist = load_watchlist()
        wants_gmail = not only or "gmail" in only or any(w.startswith("gmail:") for w in only)
        inboxes = sync_inboxes() if gmail and wants_gmail else []

        with session_scope() as session:
            radar = funded_companies(session, watchlist)
            email_sources = [
                s
                for s in build_email_sources(session, load_gmail_config())
                if not only or "gmail" in only or s.name in only
            ]
        sources = build_sources(Watchlist(companies=watchlist.companies + radar), only)
        sources += email_sources
        if not sources:
            raise NoSourcesError(f"no source matches {sorted(only or [])}")

        with session_scope() as session:
            summary = run_fetch(session, sources, search, trigger=trigger)
            for source_run in summary.sources:  # usable after the session closes
                session.expunge(source_run)
        return FetchReport(summary.run_id, summary.sources, inboxes, len(radar))
    finally:
        FETCH_LOCK.release()
