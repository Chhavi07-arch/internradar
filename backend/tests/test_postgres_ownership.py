"""Run against a disposable migrated DB with a non-owner runtime login."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from internradar import catalogue_worker
from internradar.gmail.client import Email
from internradar.mail_ingestion import accept_mail, process_mail
from internradar.models import Origin, utcnow
from internradar.ownership import owner_session
from internradar.private_mail import (
    Delivery,
    Keyring,
    delete_account,
    pending_payload,
    purge_expired,
)
from internradar.sources import Posting, Source
from internradar.user_state import update_state
from internradar.worker_lock import catalogue_refresh_lock


@pytest.fixture
def runtime():
    url = os.environ.get("INTERNRADAR_TEST_RUNTIME_URL")
    if not url:
        pytest.skip("set INTERNRADAR_TEST_RUNTIME_URL to run real PostgreSQL tests")
    engine = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) in {
                "0002_private_ownership",
                "0003_auth",
                "0004_scheduler",
                "0005_profile",
                "0006_admin",
            }
            assert not conn.scalar(
                text("""
                SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user
            """)
            )
            assert not conn.scalar(
                text("""
                SELECT pg_has_role(relowner, 'MEMBER') FROM pg_class
                WHERE oid = 'mail_delivery'::regclass
            """)
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def accounts(runtime):
    owners = [uuid4(), uuid4()]
    try:
        for owner in owners:
            with owner_session(runtime, owner) as session:
                session.execute(
                    text(
                        "INSERT INTO account (id, identity_issuer, identity_subject) "
                        "VALUES (:id, 'test', :subject)"
                    ),
                    {"id": owner, "subject": str(owner)},
                )
        yield owners
    finally:
        for owner in owners:
            with owner_session(runtime, owner) as session:
                session.execute(text("DELETE FROM account WHERE id = :id"), {"id": owner})


def _connection(session, owner, connection):
    session.execute(
        text(
            "INSERT INTO mail_connection (owner_user_id, id, provider, external_id) "
            "VALUES (:owner, :id, 'gmail', 'same-label')"
        ),
        {"owner": owner, "id": connection},
    )


def _delivery(session, owner, connection):
    session.execute(
        text(
            "INSERT INTO mail_delivery (owner_user_id, connection_id, delivery_id, received_at) "
            "VALUES (:owner, :id, 'same-message', now())"
        ),
        {"owner": owner, "id": connection},
    )


def test_isolation_and_pooled_identity_reset(runtime, accounts):
    connection = uuid4()
    for owner in accounts:
        with owner_session(runtime, owner) as session:
            _connection(session, owner, connection)
            _delivery(session, owner, connection)
            assert session.execute(
                text("SELECT owner_user_id FROM mail_delivery")
            ).scalars().all() == [owner]
    with runtime.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM mail_delivery")) == 0
    with owner_session(runtime, accounts[1]) as session:
        result = session.execute(
            text("DELETE FROM mail_delivery WHERE owner_user_id = :owner"),
            {"owner": accounts[0]},
        )
        assert result.rowcount == 0


def test_cross_owner_write_and_foreign_key_are_denied(runtime, accounts):
    first, second = accounts
    connection = uuid4()
    with owner_session(runtime, first) as session:
        _connection(session, first, connection)
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        owner_session(runtime, second) as session,
    ):
        _delivery(session, first, connection)
    with (
        pytest.raises(IntegrityError, match="foreign key"),
        owner_session(runtime, second) as session,
    ):
        _delivery(session, second, connection)


def test_public_source_run_rejects_inbox_provenance(runtime):
    with pytest.raises(IntegrityError, match="ck_sourcerun_public"), runtime.begin() as conn:
        run_id = conn.scalar(
            text("INSERT INTO run (started_at, trigger) VALUES (now(), 'test') RETURNING id")
        )
        conn.execute(
            text("""
            INSERT INTO sourcerun (run_id, source, ok, exhaustive, fetched, kept, new, duration_ms)
            VALUES (:id, 'gmail:college', true, false, 0, 0, 0, 0)
        """),
            {"id": run_id},
        )


@pytest.fixture
def inbox(runtime, accounts):
    owner = accounts[0]
    connection = uuid4()
    with owner_session(runtime, owner) as session:
        _connection(session, owner, connection)
    return Delivery(owner, connection, "test-message")


@pytest.fixture
def keys():
    return Keyring("test-v1", {"test-v1": Fernet.generate_key().decode()})


def _accept(runtime, inbox, keys):
    email = Email("m", "thread", "test@example.com", "Internship opening", utcnow(), "private body")
    return accept_mail(runtime, inbox.owner_id, inbox.connection_id, inbox.delivery_id, email, keys)


def test_normalized_delivery_is_idempotent_and_raw_is_erased(runtime, accounts, inbox, keys):
    class Extractor:
        def extract(self, email):
            assert email.body == "private body"
            return [{"company": "Example", "role": "Intern"}]

    assert _accept(runtime, inbox, keys)
    assert not _accept(runtime, inbox, keys)
    with owner_session(runtime, inbox.owner_id) as session:
        ciphertext = session.execute(text("SELECT raw_ciphertext FROM mail_delivery")).scalar_one()
        assert b"private body" not in ciphertext
    assert process_mail(runtime, inbox, Extractor(), keys)
    assert not process_mail(runtime, inbox, Extractor(), keys)
    with owner_session(runtime, inbox.owner_id) as session:
        assert (
            session.execute(text("SELECT raw_ciphertext FROM mail_delivery")).scalar_one() is None
        )
        assert session.execute(text("SELECT count(*) FROM private_opportunity")).scalar() == 1
    with owner_session(runtime, accounts[1]) as session:
        assert session.execute(text("SELECT count(*) FROM private_opportunity")).scalar() == 0


def test_expiry_removes_payload_but_keeps_delivery_identity(runtime, inbox, keys):
    assert _accept(runtime, inbox, keys)
    later = utcnow() + timedelta(days=7)
    with owner_session(runtime, inbox.owner_id) as session:
        assert pending_payload(session, inbox, keys, now=later) is None
        assert purge_expired(session, inbox.owner_id, now=later) == 1
    assert not _accept(runtime, inbox, keys)


def test_delete_retries_revocation_and_blocks_inflight_completion(runtime, inbox, keys):
    assert _accept(runtime, inbox, keys)
    version, token = keys.encrypt(b"fake-refresh-token")
    with owner_session(runtime, inbox.owner_id) as session:
        session.execute(
            text("""
            UPDATE mail_connection SET encrypted_credentials = :token, key_version = :version
        """),
            {"token": token, "version": version},
        )

    def fail(_token):
        raise OSError("provider unavailable")

    with pytest.raises(OSError, match="provider unavailable"):
        delete_account(runtime, inbox.owner_id, keys, fail, now=utcnow())
    assert not _accept(runtime, inbox, keys)
    revoked = []
    assert delete_account(runtime, inbox.owner_id, keys, revoked.append, now=utcnow())
    assert revoked == [b"fake-refresh-token"]
    with owner_session(runtime, inbox.owner_id) as session:
        for table in ("account", "mail_connection", "mail_delivery", "private_opportunity"):
            assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_account_deleted_during_extraction_discards_result(runtime, inbox, keys):
    assert _accept(runtime, inbox, keys)

    class Extractor:
        def extract(self, _email):
            assert delete_account(runtime, inbox.owner_id, keys, lambda _: None, now=utcnow())
            return [{"company": "Example", "role": "Intern"}]

    assert not process_mail(runtime, inbox, Extractor(), keys)
    with owner_session(runtime, inbox.owner_id) as session:
        assert session.execute(text("SELECT count(*) FROM private_opportunity")).scalar() == 0


def test_catalogue_lock_excludes_other_process_connections(runtime):
    other = create_engine(runtime.url)
    try:
        with catalogue_refresh_lock(runtime) as acquired:
            assert acquired
            with catalogue_refresh_lock(other) as competing:
                assert not competing
        with catalogue_refresh_lock(other) as acquired:
            assert acquired
    finally:
        other.dispose()


def test_linked_email_does_not_modify_the_public_card(runtime, accounts, inbox, keys):
    url = f"https://example.com/jobs/{uuid4()}"
    with runtime.begin() as conn:
        public_id = conn.scalar(
            text("""
            INSERT INTO opportunity (uid, kind, title, company, company_norm, title_norm,
                                     is_remote, url, primary_origin, status, first_seen_at,
                                     last_seen_at)
            VALUES (:url, 'INTERNSHIP', 'ML Intern', 'Example', 'example',
                    'machine learning intern', false, :url, 'ATS', 'ACTIVE', now(), now())
            RETURNING id
        """),
            {"url": url},
        )
    try:

        class Extractor:
            def extract(self, _mail):
                return [
                    {
                        "company": "Example",
                        "role": "ML Intern",
                        "apply_url": url,
                        "deadline": "2000-01-01",
                        "eligibility": {"min_cgpa": 10},
                    }
                ]

        with owner_session(runtime, accounts[0]) as session:
            assert update_state(session, accounts[0], public_id, notes="private note")["notes"]
            assert update_state(session, accounts[0], public_id, bookmarked=True)["notes"]
        with owner_session(runtime, accounts[1]) as session:
            state = update_state(session, accounts[1], public_id, hidden=True)
            assert state == {"seen_at": None, "bookmarked": False, "hidden": True, "notes": None}
            assert update_state(session, accounts[1], -1, bookmarked=True) is None
        assert _accept(runtime, inbox, keys)
        assert process_mail(runtime, inbox, Extractor(), keys)
        with owner_session(runtime, inbox.owner_id) as session:
            assert (
                session.execute(
                    text("SELECT public_opportunity_id FROM private_opportunity")
                ).scalar_one()
                == public_id
            )
        with owner_session(runtime, accounts[1]) as session:
            assert session.execute(text("SELECT count(*) FROM private_opportunity")).scalar() == 0
            result = session.execute(
                text("""
                SELECT status, deadline_at, eligibility FROM opportunity WHERE id = :id
            """),
                {"id": public_id},
            ).one()
            assert result == ("ACTIVE", None, None)
    finally:
        with runtime.begin() as conn:
            conn.execute(text("DELETE FROM opportunity WHERE id = :id"), {"id": public_id})


def test_public_worker_ingests_and_deduplicates(runtime, monkeypatch):
    url = f"https://example.com/jobs/{uuid4()}"

    class Board(Source):
        name, origin, exhaustive = "test-board", Origin.ATS, False

        def _fetch(self, _search):
            return [Posting(self.name, self.origin, url, "ML Intern", url, "Bengaluru", url)]

    monkeypatch.setattr(catalogue_worker, "build_sources", lambda *_: [Board()])
    assert catalogue_worker.refresh(runtime) == 1
    assert catalogue_worker.refresh(runtime) == 0
    with runtime.begin() as conn:
        ids = conn.execute(text("SELECT id FROM opportunity WHERE url = :url"), {"url": url})
        [ident] = ids.scalars().all()
        conn.execute(text("DELETE FROM sighting WHERE opportunity_id = :id"), {"id": ident})
        conn.execute(text("DELETE FROM opportunity WHERE id = :id"), {"id": ident})


def test_public_worker_rejects_private_posting_even_from_public_source(runtime, monkeypatch):
    class MixedBoard(Source):
        name, origin, exhaustive = "test-board", Origin.ATS, False

        def _fetch(self, _search):
            return [
                Posting(
                    "gmail:college", Origin.EMAIL, "mail", "ML Intern", "Example", "Bengaluru", None
                )
            ]

    monkeypatch.setattr(catalogue_worker, "build_sources", lambda *_: [MixedBoard()])
    with runtime.connect() as conn:
        before = conn.scalar(text("SELECT count(*) FROM run"))
    with pytest.raises(ValueError, match="Private mail cannot feed the public catalogue"):
        catalogue_worker.refresh(runtime)
    with runtime.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM run")) == before
