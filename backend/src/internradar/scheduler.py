"""PostgreSQL scheduler primitives; no provider credentials or payloads in job rows."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlmodel import Session

from internradar.ownership import owner_session


class Job(Enum):
    PUBLIC_ATS = "public_ats", timedelta(minutes=30)
    PUBLIC_BOARDS = "public_boards", timedelta(hours=6)
    PUBLIC_FUNDING = "public_funding", timedelta(hours=6)
    PRIVATE_PURGE = "private_purge", timedelta(minutes=15)
    PRIVATE_REVOCATION = "private_revocation", timedelta(minutes=30)

    @property
    def key(self) -> str:
        return self.value[0]

    @property
    def interval(self) -> timedelta:
        return self.value[1]

    @property
    def private(self) -> bool:
        return self in {Job.PRIVATE_PURGE, Job.PRIVATE_REVOCATION}


class Outcome(Enum):
    COMPLETE = "complete"
    DEFER = "defer"
    RETRY = "retry"
    LOST = "lost"


@dataclass(frozen=True)
class Claim:
    job: Job
    token: UUID
    failures: int
    owner_id: UUID | None


@contextmanager
def _state(engine: Engine, job: Job, owner_id: UUID | None) -> Iterator[tuple[Session, str, str]]:
    if job.private != (owner_id is not None):
        raise ValueError("private jobs require an owner; public jobs do not accept one")
    if engine.dialect.name != "postgresql":
        raise ValueError("worker scheduling requires PostgreSQL")
    if owner_id is not None:
        with owner_session(engine, owner_id) as session:
            yield session, "private_worker_job", " AND owner_user_id = :owner"
    else:
        with Session(engine) as session, session.begin():
            yield session, "public_worker_job", ""


def claim_due(
    engine: Engine,
    job: Job,
    *,
    owner_id: UUID | None = None,
    lease: timedelta = timedelta(minutes=30),
) -> Claim | None:
    """Initialize a named schedule on first use and atomically claim it if due.

    Committed before execution. Expired leases can be reclaimed after process loss;
    handlers must tolerate at-least-once execution.
    """
    if lease <= timedelta(0):
        raise ValueError("lease must be positive")
    token = uuid4()
    params = {"name": job.key, "owner": owner_id, "token": token, "lease": lease}
    with _state(engine, job, owner_id) as (session, table, scope):
        owner_column = ", owner_user_id" if owner_id is not None else ""
        owner_value = ", :owner" if owner_id is not None else ""
        session.execute(
            text(f"""
                INSERT INTO {table} (name, due_at{owner_column})
                VALUES (:name, statement_timestamp(){owner_value}) ON CONFLICT DO NOTHING
            """),
            params,
        )
        failures = session.execute(
            text(f"""
                UPDATE {table}
                SET claim_token = :token, lease_until = statement_timestamp() + :lease
                WHERE name = :name{scope} AND due_at <= statement_timestamp()
                    AND (lease_until IS NULL OR lease_until <= statement_timestamp())
                RETURNING failures
            """),
            params,
        ).scalar_one_or_none()
        return None if failures is None else Claim(job, token, failures, owner_id)


def finish(engine: Engine, claim: Claim, outcome: Outcome) -> bool:
    """Release a claim and reschedule it; a stale holder cannot change the row."""
    failures = claim.failures
    if outcome is Outcome.COMPLETE:
        failures, delay = 0, claim.job.interval
    elif outcome is Outcome.DEFER:
        delay = timedelta(seconds=15)
    elif outcome is Outcome.RETRY:
        failures = min(failures + 1, 7)
        delay = timedelta(minutes=min(2 ** (failures - 1), 60))
    else:
        raise ValueError("only completed, deferred, or failed attempts can be recorded")
    with _state(engine, claim.job, claim.owner_id) as (session, table, scope):
        return (
            session.execute(
                text(f"""
                UPDATE {table}
                SET claim_token = NULL, lease_until = NULL, failures = :failures,
                    due_at = statement_timestamp() + :delay, finished_at = statement_timestamp()
                WHERE name = :name{scope} AND claim_token = :token
                    AND lease_until > statement_timestamp()
                RETURNING 1
            """),
                {
                    "name": claim.job.key,
                    "owner": claim.owner_id,
                    "token": claim.token,
                    "delay": delay,
                    "failures": failures,
                },
            ).scalar_one_or_none()
            == 1
        )


def run_due(
    engine: Engine, job: Job, handler: Callable[[], Outcome], *, owner_id: UUID | None = None
) -> Outcome | None:
    """Drive one named job; store no exception messages or payloads, even on failure."""
    claim = claim_due(engine, job, owner_id=owner_id)
    if claim is None:
        return None
    try:
        outcome = handler()
    except Exception:
        outcome = Outcome.RETRY
    return outcome if finish(engine, claim, outcome) else Outcome.LOST
