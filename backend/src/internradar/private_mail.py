"""Persistence boundary shared by future Gmail and forwarding adapters.

Callers establish ownership with owner_session. Network extraction runs outside the write
transaction; completion checks the connection again before storing any results.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlmodel import Session

from internradar.normalize import canonical_url, normalize_company, normalize_title
from internradar.ownership import owner_session

RAW_RETENTION = timedelta(days=7)


@dataclass(frozen=True)
class Delivery:
    owner_id: UUID
    connection_id: UUID
    delivery_id: str

    @property
    def params(self) -> dict:
        return {"owner": self.owner_id, "connection": self.connection_id, "id": self.delivery_id}


class Keyring:
    """Caller supplies keys from outside the database; old versions remain decryptable."""

    def __init__(self, active: str, keys: dict[str, str]):
        self._keys = {version: Fernet(key) for version, key in keys.items()}
        if active not in self._keys:
            raise ValueError("active encryption key version is missing")
        self._active = active

    def encrypt(self, value: bytes) -> tuple[str, bytes]:
        return self._active, self._keys[self._active].encrypt(value)

    def decrypt(self, version: str, ciphertext: bytes) -> bytes:
        return self._keys[version].decrypt(ciphertext)


_CONNECTION = """
SELECT disabled_at FROM mail_connection
WHERE owner_user_id = :owner AND id = :connection FOR UPDATE
"""
_DELIVERY = "owner_user_id = :owner AND connection_id = :connection AND delivery_id = :id"
_CLEAR_RAW = "raw_ciphertext = NULL, raw_key_version = NULL, raw_expires_at = NULL"


def _active_connection(session: Session, delivery: Delivery) -> bool:
    row = session.execute(text(_CONNECTION), delivery.params).one_or_none()
    return row is not None and row.disabled_at is None


def stage_delivery(
    session: Session,
    delivery: Delivery,
    received_at: datetime,
    payload: bytes,
    keys: Keyring,
    *,
    now: datetime,
) -> bool:
    """Store pending content once. Replays do not extend the retention deadline."""
    if not _active_connection(session, delivery):
        return False
    version, ciphertext = keys.encrypt(payload)
    result = session.execute(
        text("""
            INSERT INTO mail_delivery (owner_user_id, connection_id, delivery_id, received_at,
                                       raw_ciphertext, raw_key_version, raw_expires_at)
            VALUES (:owner, :connection, :id, :received, :ciphertext, :version, :expires)
            ON CONFLICT DO NOTHING
        """),
        delivery.params
        | {
            "received": received_at,
            "ciphertext": ciphertext,
            "version": version,
            "expires": now + RAW_RETENTION,
        },
    )
    return result.rowcount == 1


def pending_payload(
    session: Session, delivery: Delivery, keys: Keyring, *, now: datetime
) -> tuple[bytes, datetime] | None:
    row = session.execute(
        text(f"""
        SELECT raw_key_version, raw_ciphertext, received_at FROM mail_delivery
        WHERE {_DELIVERY} AND raw_expires_at > :now AND EXISTS (
            SELECT 1 FROM mail_connection
            WHERE owner_user_id = :owner AND id = :connection AND disabled_at IS NULL
        )
    """),
        delivery.params | {"now": now},
    ).one_or_none()
    return (keys.decrypt(row.raw_key_version, row.raw_ciphertext), row.received_at) if row else None


def complete_delivery(
    session: Session, delivery: Delivery, kind: str, postings: list[dict], *, now: datetime
) -> bool:
    """Save validated results and delete raw content in one caller-owned transaction."""
    if not _active_connection(session, delivery):
        return False
    updated = session.execute(
        text(f"""
        UPDATE mail_delivery SET processed_at = :now, kind = :kind, {_CLEAR_RAW}
        WHERE {_DELIVERY} AND processed_at IS NULL AND raw_expires_at > :now
    """),
        delivery.params | {"now": now, "kind": kind},
    )
    if updated.rowcount != 1:
        return False
    if postings:
        session.execute(
            text("""
            INSERT INTO private_opportunity (owner_user_id, connection_id, delivery_id,
                                             position, fields, public_opportunity_id)
            VALUES (:owner, :connection, :id, :position, CAST(:fields AS json), :public_id)
        """),
            [
                delivery.params
                | {
                    "position": index,
                    "fields": json.dumps(fields),
                    "public_id": _match_public(session, fields),
                }
                for index, fields in enumerate(postings)
            ],
        )
    return True


def _match_public(session: Session, fields: dict) -> int | None:
    """Link by URL, company and title only when there's one unambiguous public match."""
    url = canonical_url(fields.get("apply_url"))
    if not url:
        return None
    rows = (
        session.execute(
            text("""
            SELECT o.id FROM opportunity o
            WHERE o.company_norm = :company AND o.title_norm = :title
            AND (o.url = :url OR EXISTS (
                SELECT 1 FROM sighting s WHERE s.opportunity_id = o.id AND s.url = :url
            )) LIMIT 2
        """),
            {
                "url": url,
                "company": normalize_company(fields["company"]),
                "title": normalize_title(fields["role"]),
            },
        )
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def purge_expired(session: Session, owner_id: UUID, *, now: datetime) -> int:
    """Keep the deduplication record, but remove content past the retry window."""
    result = session.execute(
        text(f"""
        UPDATE mail_delivery SET {_CLEAR_RAW}
        WHERE owner_user_id = :owner AND raw_expires_at <= :now
    """),
        {"owner": owner_id, "now": now},
    )
    return result.rowcount


def disconnect(
    engine: Engine,
    owner_id: UUID,
    connection_id: UUID,
    keys: Keyring,
    revoke: Callable[[bytes], None],
    *,
    now: datetime,
) -> None:
    """Disable before external revocation. Failures leave an encrypted retryable token.

    The revoke callback must handle already-revoked credentials as success.
    """
    params = {"owner": owner_id, "connection": connection_id, "now": now}
    with owner_session(engine, owner_id) as session:
        row = session.execute(
            text("""
            UPDATE mail_connection SET disabled_at = COALESCE(disabled_at, :now)
            WHERE owner_user_id = :owner AND id = :connection
            RETURNING encrypted_credentials, key_version
        """),
            params,
        ).one_or_none()
        session.execute(
            text(f"""
            UPDATE mail_delivery SET {_CLEAR_RAW}
            WHERE owner_user_id = :owner AND connection_id = :connection
        """),
            params,
        )
    if row is None or row.encrypted_credentials is None:
        return
    revoke(keys.decrypt(row.key_version, row.encrypted_credentials))
    with owner_session(engine, owner_id) as session:
        session.execute(
            text("""
            UPDATE mail_connection SET encrypted_credentials = NULL, key_version = NULL
            WHERE owner_user_id = :owner AND id = :connection AND disabled_at IS NOT NULL
                  AND encrypted_credentials = :previous
        """),
            params | {"previous": row.encrypted_credentials},
        )


def delete_account(
    engine: Engine,
    owner_id: UUID,
    keys: Keyring,
    revoke: Callable[[bytes], None],
    *,
    now: datetime,
) -> bool:
    """Stop all inbox work before revoking; retry failures without losing the token."""
    params = {"owner": owner_id, "now": now}
    with owner_session(engine, owner_id) as session:
        connection_ids = (
            session.execute(
                text("""
            UPDATE mail_connection SET disabled_at = COALESCE(disabled_at, :now)
            WHERE owner_user_id = :owner RETURNING id
        """),
                params,
            )
            .scalars()
            .all()
        )
        session.execute(
            text(f"""
            UPDATE mail_delivery SET {_CLEAR_RAW} WHERE owner_user_id = :owner
        """),
            params,
        )
    for connection_id in connection_ids:
        disconnect(engine, owner_id, connection_id, keys, revoke, now=now)
    with owner_session(engine, owner_id) as session:
        deleted = session.execute(
            text("""
            DELETE FROM account WHERE id = :owner AND NOT EXISTS (
                SELECT 1 FROM mail_connection WHERE owner_user_id = :owner
                AND (disabled_at IS NULL OR encrypted_credentials IS NOT NULL)
            )
        """),
            params,
        )
        return deleted.rowcount == 1
