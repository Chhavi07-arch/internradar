"""Admin isolation; real PostgreSQL tests require a fresh, disposable ir_admin_test DB."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from internradar import admin
from internradar.admin_operator import _change_role
from internradar.ownership import owner_session
from internradar.web_auth import ISSUER, Principal, WebSettings, create_session

SETTINGS = WebSettings("http://localhost:8448", "dev", "secret", "s" * 32, local_http=True)


def _client(engine):
    app = FastAPI()
    admin.install_admin(app, engine, SETTINGS)
    return TestClient(app)


def test_unconfigured_or_wrong_login_is_unavailable(monkeypatch):
    monkeypatch.setattr(admin, "principal_for", lambda *_: Principal(uuid4(), "admin"))
    runtime = create_engine("postgresql+psycopg://test_web@127.0.0.1/test_web_db")
    for url in (
        None,
        "postgresql+psycopg://db_owner@localhost/db",
        "postgresql+psycopg://internradar_metrics_reader@127.0.0.1/other_db",
        "malformed",
    ):
        if url is None:
            monkeypatch.delenv("INTERNRADAR_METRICS_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("INTERNRADAR_METRICS_DATABASE_URL", url)
        with _client(runtime) as client:
            response = client.get("/api/admin/metrics")
        assert response.status_code == 503
        assert response.headers["Cache-Control"] == "no-store"


def test_anonymous_request_cannot_reach_metrics(monkeypatch):
    def unreachable():
        raise AssertionError("attempted connection before session verification")

    monkeypatch.setattr(
        admin, "_metrics_engine", lambda _: type("Engine", (), {"connect": unreachable})()
    )
    with _client(None) as client:
        assert (
            client.get("/api/admin/metrics?role=admin", headers={"X-Role": "admin"}).status_code
            == 401
        )


@pytest.fixture
def database(monkeypatch):
    configured = os.getenv("INTERNRADAR_TEST_ADMIN_MIGRATION_URL")
    if not configured:
        pytest.skip("set INTERNRADAR_TEST_ADMIN_MIGRATION_URL to a fresh migrated ir_admin_test DB")
    url = make_url(configured)
    assert url.database == "ir_admin_test" and url.host in {"127.0.0.1", "::1", "localhost"}
    assert not url.query, "test connection must not have libpq overrides"
    operator = create_engine(url, hide_parameters=True)
    runtime = create_engine(
        url.set(username="internradar_admin_test_runtime"), hide_parameters=True
    )
    reader_url = url.set(username="internradar_metrics_reader")
    reader = create_engine(reader_url, hide_parameters=True)
    setup_complete = False
    try:
        with operator.begin() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0006_admin"
            assert conn.scalar(text("SELECT count(*) FROM public.account")) == 0
            assert conn.scalar(text("SELECT count(*) FROM internradar_admin.role_audit")) == 0
            assert conn.scalar(text("SELECT count(*) FROM public.run")) == 0
            assert conn.scalar(text("SELECT count(*) FROM public.opportunity")) == 0
            assert conn.scalar(text("SELECT count(*) FROM public.fundingevent")) == 0
            assert (
                conn.scalar(
                    text(
                        "SELECT rolcanlogin FROM pg_roles "
                        "WHERE rolname = 'internradar_metrics_reader'"
                    )
                )
                is False
            )
            conn.execute(text("CREATE ROLE internradar_admin_test_runtime LOGIN NOBYPASSRLS"))
            conn.execute(text("ALTER ROLE internradar_metrics_reader LOGIN"))
            conn.execute(
                text("""
                GRANT SELECT, INSERT (id, identity_issuer, identity_subject) ON public.account
                    TO internradar_admin_test_runtime
            """)
            )
            conn.execute(
                text("""
                GRANT SELECT, INSERT, DELETE ON public.web_session TO internradar_admin_test_runtime
            """)
            )
            conn.execute(
                text("""
                GRANT SELECT, INSERT, UPDATE ON public.student_profile
                    TO internradar_admin_test_runtime
            """)
            )
        setup_complete = True
        monkeypatch.setenv(
            "INTERNRADAR_METRICS_DATABASE_URL", reader_url.render_as_string(hide_password=False)
        )
        yield operator, runtime, reader
    finally:
        runtime.dispose()
        reader.dispose()
        if setup_complete:
            with operator.begin() as conn:
                conn.execute(text("DELETE FROM internradar_admin.role_audit"))
                conn.execute(text("DELETE FROM public.sourcerun"))
                conn.execute(text("DELETE FROM public.run"))
                conn.execute(text("DELETE FROM public.account"))
                conn.execute(text("DROP OWNED BY internradar_admin_test_runtime"))
                conn.execute(text("DROP ROLE internradar_admin_test_runtime"))
                conn.execute(text("ALTER ROLE internradar_metrics_reader NOLOGIN"))
        operator.dispose()


def _account(runtime, issuer=ISSUER):
    subject = str(uuid4())
    token = create_session(runtime, issuer, subject)
    with runtime.connect() as conn:
        from internradar.web_auth import _digest

        account_id = conn.scalar(
            text("SELECT owner_user_id FROM public.web_session WHERE token_hash = :hash"),
            {"hash": _digest(token)},
        )
    return account_id, token, subject


def test_metrics_enrollment_revocation_and_isolation(database):
    operator, runtime, reader = database
    first, token, subject = _account(runtime)
    second, member_token, _ = _account(runtime)
    outside_google, _, _ = _account(runtime, issuer="other")
    with pytest.raises(ValueError, match="No matching Google account"):
        _change_role(operator, outside_google, grant=True)
    with pytest.raises(ValueError, match="No matching Google account"):
        _change_role(operator, uuid4(), grant=True)
    with operator.begin() as conn:
        for hours, ok in ((2, True), (1, False)):
            run = conn.scalar(
                text("""
                INSERT INTO public.run (started_at, finished_at, trigger)
                VALUES (now() - make_interval(hours => :hours),
                        now() - make_interval(hours => :hours) + interval '10 seconds', 'test')
                RETURNING id
            """),
                {"hours": hours},
            )
            conn.execute(
                text("""
                INSERT INTO public.sourcerun
                    (run_id, source, ok, exhaustive, fetched, kept, new, duration_ms, error)
                VALUES (:run, 'synthetic:source', :ok, false, 0, 0, 0, 0, 'private-looking error')
            """),
                {"run": run, "ok": ok},
            )
    with runtime.connect() as conn:
        admin.verify_web_account_acl(conn)
    with _client(runtime) as client:
        client.cookies.set(SETTINGS.session_cookie, member_token)
        assert (
            client.get("/api/admin/metrics?role=admin", headers={"X-Role": "admin"}).status_code
            == 403
        )
        assert _change_role(operator, first, grant=True)
        assert not _change_role(operator, first, grant=True)
        client.cookies.set(SETTINGS.session_cookie, token)
        result = client.get("/api/admin/metrics")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["current_accounts"] == 3
        assert body["catalogue_listings"] == 0
        assert body["funding_events_last_30_days"] == 0
        [source] = body["sources"]
        assert source["source"] == "synthetic:source"
        assert source["latest_attempt_ok"] is False
        assert source["last_successful_refresh_at"] < source["latest_attempt_at"]
        assert not any(
            x in result.text for x in (str(first), str(second), subject, "private-looking error")
        )
        with owner_session(runtime, first) as session:
            assert session.execute(text("SELECT id FROM public.account")).scalars().all() == [first]
            assert session.execute(
                text("SELECT owner_user_id FROM public.student_profile")
            ).scalars().all() == [first]
        for query in (
            "SELECT identity_subject FROM public.account",
            "SELECT * FROM public.student_profile",
            "SELECT * FROM public.opportunity",
            "SELECT * FROM internradar_admin.role_audit",
        ):
            with pytest.raises(DBAPIError, match="permission denied"), reader.connect() as conn:
                conn.execute(text(query))
        with pytest.raises(DBAPIError, match="permission denied"), runtime.connect() as conn:
            conn.execute(text("SELECT internradar_admin.metrics()"))
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            owner_session(runtime, first) as session,
        ):
            session.execute(text("UPDATE public.account SET role = 'customer'"))
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            owner_session(runtime, first) as session,
        ):
            session.execute(
                text("""
                INSERT INTO public.account (id, identity_issuer, identity_subject, role)
                VALUES (gen_random_uuid(), 'test', 'forgery', 'admin')
            """)
            )
        with operator.begin() as conn:
            conn.execute(text("GRANT SELECT (id) ON public.account TO internradar_metrics_reader"))
        try:
            assert client.get("/api/admin/metrics").status_code == 503
        finally:
            with operator.begin() as conn:
                conn.execute(
                    text("REVOKE SELECT (id) ON public.account FROM internradar_metrics_reader")
                )
        assert _change_role(operator, first, grant=False)
        assert client.get("/api/admin/metrics").status_code == 403
        client.cookies.set(SETTINGS.session_cookie, create_session(runtime, ISSUER, subject))
        assert client.get("/api/admin/metrics").status_code == 403
    with operator.connect() as conn:
        audit = conn.execute(
            text("""
            SELECT operator_role = session_user, target_account_id, previous_role, new_role
            FROM internradar_admin.role_audit ORDER BY id
        """)
        ).all()
        assert audit == [(True, first, "customer", "admin"), (True, first, "admin", "customer")]
