"""Serve opportunities extracted from email to the fetch pipeline like any other source.

Extraction already happened during sync, so this source only reads the database. Returning
every recent email posting on each run is harmless: the pipeline recognises (source,
native_id) pairs it has seen and just refreshes them.
"""

from datetime import timedelta
from typing import Any

from sqlmodel import Session, col, select

from internradar.config import GmailConfig, SearchConfig
from internradar.gmail.extract import deadline_datetime
from internradar.models import EmailMessage, Origin, utcnow
from internradar.sources.base import Posting, Source


class EmailSource(Source):
    origin = Origin.EMAIL
    exhaustive = False

    def __init__(self, label: str, postings: list[Posting]):
        self.name = f"gmail:{label}"
        self._postings = postings

    def _fetch(self, search: SearchConfig) -> list[Posting]:
        return self._postings


def build_email_sources(session: Session, config: GmailConfig) -> list[EmailSource]:
    since = utcnow() - timedelta(days=config.lookback_days)
    sources = []
    for account in config.accounts:
        rows = session.exec(
            select(EmailMessage).where(
                EmailMessage.account == account.label,
                col(EmailMessage.extracted).is_not(None),
                EmailMessage.received_at >= since,
            )
        ).all()
        postings = [
            to_posting(account.label, account.trusted, row, i, item)
            for row in rows
            for i, item in enumerate(row.extracted or [])
        ]
        if rows:
            sources.append(EmailSource(account.label, postings))
    return sources


def to_posting(
    label: str, trusted: bool, row: EmailMessage, index: int, item: dict[str, Any]
) -> Posting:
    notes = (item.get("eligibility") or {}).get("notes")
    description = "\n".join(
        part
        for part in (
            f"From email: {row.subject}",
            item.get("stipend") and f"Stipend: {item['stipend']}",
            notes,
        )
        if part
    )
    return Posting(
        source=f"gmail:{label}",
        origin=Origin.EMAIL,
        native_id=f"{row.gmail_id}#{index}",
        title=item["role"],
        company=item["company"],
        location=item.get("location"),
        url=item.get("apply_url"),
        apply_url=item.get("apply_url"),
        description=description,
        posted_at=row.received_at,
        deadline_at=deadline_datetime(item.get("deadline")),
        is_remote=bool(item.get("is_remote")),
        flagged_internship=bool(item.get("is_internship")),
        eligibility=item.get("eligibility"),
        trusted=trusted,
    )
