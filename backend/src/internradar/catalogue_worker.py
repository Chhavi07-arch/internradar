"""Run public catalogue refreshes once or on a durable PostgreSQL schedule."""

import argparse
import logging
import os
from functools import partial
from time import sleep

from sqlalchemy import Engine, create_engine
from sqlmodel import Session

from internradar.config import Watchlist, load_search_config, load_watchlist
from internradar.funding.radar import funded_companies, make_client, run_radar
from internradar.pipeline import run_fetch
from internradar.scheduler import Job, Outcome, run_due
from internradar.sources import build_sources
from internradar.worker_lock import catalogue_refresh_lock

log = logging.getLogger(__name__)
_PUBLIC_JOBS = (
    (Job.PUBLIC_ATS, False, {"ats"}),
    (Job.PUBLIC_BOARDS, False, {"boards"}),
    (Job.PUBLIC_FUNDING, True, None),
)


def _refresh(engine: Engine, *, funding: bool, only: set[str] | None) -> tuple[int, bool] | None:
    with catalogue_refresh_lock(engine) as connection:
        if connection is None:
            return None
        watchlist = load_watchlist()
        with Session(connection) as session:
            if funding:
                with make_client() as client:
                    result = run_radar(session, watchlist, client)
                session.flush()
                return 0, all(isinstance(count, int) for count in result.feeds.values())
            discovered = funded_companies(session, watchlist)
            sources = build_sources(Watchlist(companies=watchlist.companies + discovered), only)
            if not sources:
                raise ValueError("no matching public sources")
            result = run_fetch(session, sources, load_search_config(), trigger="worker")
            return result.new, all(source.ok for source in result.sources)


def refresh(engine: Engine, *, funding: bool = False, only: set[str] | None = None) -> int | None:
    result = _refresh(engine, funding=funding, only=only)
    return None if result is None else result[0]


def _scheduled_refresh(engine: Engine, *, funding: bool, only: set[str] | None) -> Outcome:
    result = _refresh(engine, funding=funding, only=only)
    if result is None:
        return Outcome.DEFER
    return Outcome.COMPLETE if result[1] else Outcome.RETRY


def run_public_jobs(engine: Engine) -> dict[Job, Outcome]:
    """Run each due public job once; retain successful data from partially failed fetches."""
    outcomes = {}
    for job, funding, only in _PUBLIC_JOBS:
        outcome = run_due(
            engine, job, partial(_scheduled_refresh, engine, funding=funding, only=only)
        )
        if outcome is not None:
            outcomes[job] = outcome
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--funding", action="store_true")
    parser.add_argument("--only", action="append")
    parser.add_argument("--schedule", action="store_true", help="poll for due public jobs")
    parser.add_argument("--once", action="store_true", help="poll once and exit (with --schedule)")
    args = parser.parse_args()
    if args.schedule and (args.funding or args.only):
        parser.error("--schedule cannot be combined with --funding or --only")
    if args.once and not args.schedule:
        parser.error("--once requires --schedule")
    engine = create_engine(os.environ["INTERNRADAR_CATALOGUE_DATABASE_URL"], pool_pre_ping=True)
    try:
        if args.schedule:
            logging.basicConfig(level=logging.INFO)
            while True:
                for job, outcome in run_public_jobs(engine).items():
                    level = logging.INFO if outcome is Outcome.COMPLETE else logging.WARNING
                    log.log(level, "%s: %s", job.key, outcome.value)
                if args.once:
                    return
                sleep(15)
        new = refresh(engine, funding=args.funding, only=set(args.only) if args.only else None)
        if new is None:
            print("Skipped: another public refresh is running")
        elif args.funding:
            print("Funding refresh complete")
        else:
            print(f"Public refresh complete: {new} new listings")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
