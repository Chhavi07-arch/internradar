"""Fixtures exercise real PostgreSQL claims under a non-owner, non-BYPASSRLS login."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from internradar import catalogue_worker
from internradar.funding.radar import RadarSummary
from internradar.ownership import owner_session
from internradar.private_mail import Keyring
from internradar.private_worker import run_private_jobs
from internradar.scheduler import Job, Outcome, claim_due, finish
from internradar.worker_lock import catalogue_refresh_lock


@pytest.fixture
def engine():
    url = os.environ.get("INTERNRADAR_TEST_RUNTIME_URL")
    if not url:
        pytest.skip("set INTERNRADAR_TEST_RUNTIME_URL to use a disposable migrated database")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
            if revision in {"0002_private_ownership", "0003_auth"}:
                pytest.skip("scheduler tests require 0004_scheduler")
            assert revision == "0004_scheduler"
            assert not conn.scalar(
                text("""
                SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user
            """)
            )
            assert not conn.scalar(
                text("""
                SELECT pg_has_role(relowner, 'MEMBER') FROM pg_class
                WHERE oid = 'private_worker_job'::regclass
            """)
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def public(engine):
    def reset():
        with engine.begin() as conn:
            conn.execute(
                text("""
                UPDATE public_worker_job SET due_at = now(), claim_token = NULL, lease_until = NULL,
                    failures = 0, finished_at = NULL
            """)
            )

    reset()
    yield engine
    reset()


def test_claim_is_exclusive_and_lease_recovery_is_fenced(public):
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return claim_due(public, Job.PUBLIC_ATS)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        (first,) = [result for f in futures if (result := f.result()) is not None]
    assert claim_due(public, Job.PUBLIC_ATS) is None
    with public.begin() as conn:
        conn.execute(
            text("UPDATE public_worker_job SET lease_until = now() WHERE name = :job"),
            {"job": Job.PUBLIC_ATS.key},
        )
    assert not finish(public, first, Outcome.COMPLETE)
    second = claim_due(public, Job.PUBLIC_ATS)
    assert second is not None and second.token != first.token
    assert not finish(public, first, Outcome.COMPLETE)
    assert finish(public, second, Outcome.COMPLETE)
    assert claim_due(public, Job.PUBLIC_ATS) is None
    with public.connect() as conn:
        assert conn.scalar(
            text("""
            SELECT due_at - finished_at FROM public_worker_job WHERE name = :job
        """),
            {"job": Job.PUBLIC_ATS.key},
        ) == timedelta(minutes=30)


def test_public_schedule_dispatch_and_retry(public, monkeypatch):
    work = []

    def build(_watchlist, only):
        work.extend(only)
        return [object()]

    def fetch(*_args, **_kwargs):
        if work[-1] == "boards":
            raise RuntimeError("synthetic failure")
        return type("Summary", (), {"new": 0, "sources": []})()

    def funding(*_args):
        work.append("funding")
        return RadarSummary(feeds={"source": "synthetic feed failure"})

    monkeypatch.setattr(catalogue_worker, "build_sources", build)
    monkeypatch.setattr(catalogue_worker, "run_fetch", fetch)
    monkeypatch.setattr(catalogue_worker, "make_client", nullcontext)
    monkeypatch.setattr(catalogue_worker, "run_radar", funding)
    with catalogue_refresh_lock(public):
        assert catalogue_worker.run_public_jobs(public) == {
            Job.PUBLIC_ATS: Outcome.DEFER,
            Job.PUBLIC_BOARDS: Outcome.DEFER,
            Job.PUBLIC_FUNDING: Outcome.DEFER,
        }
    assert not work
    with public.begin() as conn:
        conn.execute(text("UPDATE public_worker_job SET due_at = now()"))
    assert catalogue_worker.run_public_jobs(public) == {
        Job.PUBLIC_ATS: Outcome.COMPLETE,
        Job.PUBLIC_BOARDS: Outcome.RETRY,
        Job.PUBLIC_FUNDING: Outcome.RETRY,
    }
    assert work == ["ats", "boards", "funding"]
    assert catalogue_worker.run_public_jobs(public) == {}
    with public.connect() as conn:
        assert conn.execute(
            text("""
            SELECT failures, due_at - finished_at FROM public_worker_job WHERE name = :job
        """),
            {"job": Job.PUBLIC_FUNDING.key},
        ).one() == (1, timedelta(minutes=1))


def test_private_jobs_retry_encrypted_tokens_and_purge_only_owner(engine):
    a, b, connection, active = (uuid4() for _ in range(4))
    key = Fernet.generate_key()
    fernet = Fernet(key)
    keys = Keyring("v1", {"v1": key.decode()})
    try:
        for owner, token in ((a, b"owner-a"), (b, b"owner-b")):
            with owner_session(engine, owner) as session:
                session.execute(
                    text("""
                    INSERT INTO account (id, identity_issuer, identity_subject)
                    VALUES (:owner, 'scheduler-test', :subject)
                """),
                    {"owner": owner, "subject": str(owner)},
                )
                session.execute(
                    text("""
                    INSERT INTO mail_connection (owner_user_id, id, provider, external_id,
                        disabled_at, encrypted_credentials, key_version)
                    VALUES (:owner, :connection, 'gmail', 'disabled', now(), :token, 'v1'),
                           (:owner, :active, 'gmail', 'active', NULL, :token, 'v1')
                """),
                    {
                        "owner": owner,
                        "connection": connection,
                        "active": active,
                        "token": fernet.encrypt(token),
                    },
                )
                session.execute(
                    text("""
                    INSERT INTO mail_delivery (owner_user_id, connection_id, delivery_id,
                        received_at, raw_ciphertext, raw_key_version, raw_expires_at)
                    VALUES (:owner, :active, 'expired', now(), :raw, 'v1', now()-interval '1 day'),
                           (:owner, :active, 'current', now(), :raw, 'v1', now()+interval '1 day')
                """),
                    {"owner": owner, "active": active, "raw": fernet.encrypt(b"body")},
                )

        calls = []

        def fail(token):
            calls.append(token)
            raise RuntimeError("fake provider failure")

        assert run_private_jobs(engine, a, keys=keys, revoke=fail) == {
            Job.PRIVATE_PURGE: Outcome.COMPLETE,
            Job.PRIVATE_REVOCATION: Outcome.RETRY,
        }
        assert calls == [b"owner-a"]
        assert run_private_jobs(engine, a, keys=keys, revoke=calls.append) == {}
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM private_worker_job")) == 0
        with owner_session(engine, b) as session:
            assert session.scalar(text("SELECT count(*) FROM private_worker_job")) == 0
            assert session.scalar(
                text("SELECT bool_and(raw_ciphertext IS NOT NULL) FROM mail_delivery")
            )
        with pytest.raises(DBAPIError), owner_session(engine, b) as session:
            session.execute(
                text("""
                INSERT INTO private_worker_job(owner_user_id, name, due_at)
                VALUES (:a, 'private_revocation', now())
            """),
                {"a": a},
            )
        with owner_session(engine, a) as session:
            assert session.execute(
                text("""
                SELECT delivery_id, raw_ciphertext IS NULL FROM mail_delivery ORDER BY delivery_id
            """)
            ).all() == [("current", False), ("expired", True)]
            assert session.scalar(
                text("""
                SELECT encrypted_credentials IS NOT NULL FROM mail_connection
                WHERE disabled_at IS NOT NULL
            """)
            )
            session.execute(
                text("""
                UPDATE private_worker_job SET due_at = now() WHERE name = 'private_revocation'
            """)
            )
        assert run_private_jobs(engine, a, keys=keys, revoke=calls.append) == {
            Job.PRIVATE_REVOCATION: Outcome.COMPLETE,
        }
        assert calls == [b"owner-a", b"owner-a"]
        with owner_session(engine, a) as session:
            assert session.execute(
                text("""
                SELECT external_id, encrypted_credentials IS NULL FROM mail_connection
                ORDER BY external_id
            """)
            ).all() == [("active", False), ("disabled", True)]
    finally:
        for owner in (a, b):
            with owner_session(engine, owner) as session:
                session.execute(text("DELETE FROM account WHERE id = :owner"), {"owner": owner})
