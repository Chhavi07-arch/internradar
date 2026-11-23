"""Database schema.

One `Opportunity` is one real-world role. Each place it was seen (a LinkedIn listing, a
Greenhouse board, a job-alert email) is a `Sighting`, so the dashboard shows a single card
while remembering every source. Fields under "user state" are only ever written by the user,
never by a fetch.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Kind(StrEnum):
    INTERNSHIP = "internship"
    JOB = "job"


class Origin(StrEnum):
    """Where a sighting came from. Lower rank wins when sources disagree on a field."""

    ATS = "ats"  # the company's own careers board: most accurate
    LIST = "list"  # curated lists such as SimplifyJobs
    BOARD = "board"  # LinkedIn, Indeed, Naukri...
    EMAIL = "email"  # college or personal inbox

    @property
    def rank(self) -> int:
        return list(Origin).index(self)


class Lifecycle(StrEnum):
    NEW = "new"  # first seen in the latest run
    ACTIVE = "active"
    CLOSED = "closed"  # every source has stopped listing it


class Opportunity(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    uid: str = Field(unique=True, index=True, description="normalize.fingerprint()")
    kind: Kind = Kind.INTERNSHIP

    title: str
    company: str
    company_norm: str = Field(index=True)
    title_norm: str
    location: str | None = None
    city: str | None = Field(default=None, index=True)
    is_remote: bool = False

    url: str | None = None
    apply_url: str | None = None
    description: str | None = Field(default=None, sa_column=Column(Text))
    stipend_min: float | None = None
    stipend_max: float | None = None
    currency: str | None = None
    pay_period: str | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    # {"min_cgpa": 7.0, "branches": ["CSE"], "batches": [2028], "backlogs_allowed": false, ...}
    eligibility: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    primary_origin: Origin
    status: Lifecycle = Field(default=Lifecycle.NEW, index=True)
    first_seen_at: datetime = Field(default_factory=utcnow, index=True)
    last_seen_at: datetime = Field(default_factory=utcnow)

    # user state
    seen_at: datetime | None = None  # opened in the dashboard; unset means "unread"
    bookmarked: bool = False
    hidden: bool = False
    notes: str | None = None


class Sighting(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "native_id"),)

    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    source: str = Field(description='e.g. "linkedin" or "greenhouse:anthropic"')
    origin: Origin
    native_id: str
    url: str | None = Field(default=None, index=True)
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    missed_runs: int = 0
    closed_at: datetime | None = None


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    trigger: str = "cli"


class SourceRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    source: str
    ok: bool
    exhaustive: bool = Field(description="True if the source returns its full listing")
    fetched: int = 0  # postings the source returned
    kept: int = 0  # ...that passed the relevance filter
    new: int = 0  # ...that became new opportunities
    duration_ms: int = 0
    error: str | None = None


class FundingEvent(SQLModel, table=True):
    """A startup funding round seen in the news; a recent round is a strong hiring signal."""

    id: int | None = Field(default=None, primary_key=True)
    company: str
    company_norm: str = Field(index=True)
    round: str | None = None
    amount_text: str | None = None
    amount_usd: float | None = None
    lead_investors: str | None = None
    is_tech: bool = False
    headline: str  # first headline seen, shown on the dashboard
    url: str
    published_at: datetime = Field(default_factory=utcnow, index=True)
    discovered_at: datetime = Field(default_factory=utcnow)

    # careers board, found automatically and verified (see funding/boards.py)
    board_ats: str | None = None
    board_slug: str | None = None
    board_checked_at: datetime | None = None


class FundingMention(SQLModel, table=True):
    """One article about a funding event; several outlets usually cover the same round."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="fundingevent.id", index=True)
    url: str = Field(unique=True)
    feed: str
    headline: str


class EmailKind(StrEnum):
    JOB_ALERT = "job_alert"  # LinkedIn / Naukri / Internshala alert digests
    OPPORTUNITY = "opportunity"  # placement-cell mail and direct invitations
    STATUS = "status"  # replies about an application (used by application tracking)
    OTHER = "other"


class EmailMessage(SQLModel, table=True):
    """An email read from a connected inbox. Bodies stay in the local database only."""

    __table_args__ = (UniqueConstraint("account", "gmail_id"),)

    id: int | None = Field(default=None, primary_key=True)
    account: str = Field(index=True)
    gmail_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime = Field(index=True)
    kind: EmailKind = Field(index=True)
    body: str = Field(sa_column=Column(Text))

    # Opportunities extracted by the LLM, already validated against the email text.
    extracted: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    extracted_at: datetime | None = None
    extract_error: str | None = None
