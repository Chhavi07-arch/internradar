"""Inbox sync: new messages → triage → LLM extraction → stored, ready for the fetch pipeline.

Each message is downloaded and extracted once. Messages whose extraction failed (or that
arrived while no API key was configured) stay pending and are retried on the next sync.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from sqlmodel import Session, col, select

from internradar.config import GmailAccount
from internradar.gmail.classify import classify
from internradar.gmail.client import Email, GmailClient
from internradar.gmail.extract import Extractor
from internradar.models import EmailKind, EmailMessage, utcnow

MAX_PARALLEL_DOWNLOADS = 6
MAX_PARALLEL_EXTRACTIONS = 4
_EXTRACTABLE = (EmailKind.JOB_ALERT, EmailKind.OPPORTUNITY)


@dataclass
class SyncResult:
    account: str
    listed: int = 0
    new: int = 0
    extracted: int = 0
    opportunities: int = 0
    pending: int = 0
    errors: list[str] = field(default_factory=list)


def sync_account(
    session: Session,
    account: GmailAccount,
    gmail: GmailClient,
    extractor: Extractor | None,
    lookback_days: int,
) -> SyncResult:
    result = SyncResult(account=account.label)
    ids = gmail.list_ids(f"{account.query} newer_than:{lookback_days}d")
    result.listed = len(ids)
    known = set(
        session.exec(
            select(EmailMessage.gmail_id).where(
                EmailMessage.account == account.label, col(EmailMessage.gmail_id).in_(ids)
            )
        )
    )
    new_ids = [i for i in ids if i not in known]

    with ThreadPoolExecutor(MAX_PARALLEL_DOWNLOADS) as pool:
        downloads = list(pool.map(lambda i: _try(gmail.get, i), new_ids))
    for email, error in downloads:
        if error:
            result.errors.append(error)
            continue
        session.add(_to_row(account.label, email))
        result.new += 1
    session.flush()

    pending = session.exec(
        select(EmailMessage).where(
            EmailMessage.account == account.label,
            col(EmailMessage.kind).in_(_EXTRACTABLE),
            col(EmailMessage.extracted_at).is_(None),
            EmailMessage.received_at >= utcnow() - timedelta(days=lookback_days),
        )
    ).all()
    if extractor is None:
        result.pending = len(pending)
        return result

    with ThreadPoolExecutor(MAX_PARALLEL_EXTRACTIONS) as pool:
        outcomes = list(pool.map(lambda row: _try(extractor.extract, to_email(row)), pending))
    for row, (opportunities, error) in zip(pending, outcomes, strict=True):
        if error:
            row.extract_error = error
            result.pending += 1
            continue
        row.extracted, row.extracted_at, row.extract_error = opportunities, utcnow(), None
        result.extracted += 1
        result.opportunities += len(opportunities)
    session.flush()
    return result


def _try(fn, arg):
    try:
        return fn(arg), None
    except Exception as exc:  # one bad message must not stop the sync
        return None, f"{type(exc).__name__}: {exc}"[:300]


def _to_row(account: str, email: Email) -> EmailMessage:
    return EmailMessage(
        account=account,
        gmail_id=email.gmail_id,
        thread_id=email.thread_id,
        sender=email.sender,
        subject=email.subject,
        received_at=email.received_at,
        kind=classify(email),
        body=email.body,
    )


def to_email(row: EmailMessage) -> Email:
    return Email(
        gmail_id=row.gmail_id,
        thread_id=row.thread_id,
        sender=row.sender,
        subject=row.subject,
        received_at=row.received_at,
        body=row.body,
    )
