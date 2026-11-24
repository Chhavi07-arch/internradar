import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from internradar.hosted import create_hosted_app
from internradar.ownership import owner_session
from internradar.web_auth import COOKIE, ISSUER, WebSettings, create_session, principal_for

ORIGIN = "https://internradar.test"


@pytest.fixture
def hosted():
    url = os.getenv("INTERNRADAR_TEST_RUNTIME_URL")
    if not url:
        pytest.skip("requires disposable migrated PostgreSQL")
    engine = create_engine(url)
    app = create_hosted_app(engine, WebSettings(ORIGIN, "test-id", "secret", "x" * 48))
    owners = []
    clients = []
    try:
        for _ in range(2):
            token = create_session(engine, ISSUER, str(uuid4()))
            owners.append(principal_for(engine, token).id)
            clients.append(
                TestClient(
                    app, base_url=ORIGIN, cookies={COOKIE: token}, headers={"Origin": ORIGIN}
                )
            )
        yield engine, app, owners, clients
    finally:
        for owner in owners:
            with owner_session(engine, owner) as session:
                session.execute(text("DELETE FROM account WHERE id = :id"), {"id": owner})
        engine.dispose()


def test_sessions_and_profiles_are_independent(hosted):
    _, app, _, (alice, bob) = hosted
    anonymous = TestClient(app, base_url=ORIGIN)
    assert anonymous.get("/api/overview").status_code == 401
    assert alice.get("/api/session").json() == {"role": "customer"}
    assert alice.put("/api/profile", json={"name": "Alice", "batch": 2028}).status_code == 200
    assert alice.get("/api/profile").json()["name"] == "Alice"
    assert bob.get("/api/profile").json() == {}
    assert bob.put("/api/profile", json={"role": "admin"}).status_code == 422
    assert alice.post("/auth/logout", headers={"Origin": "https://other.test"}).status_code == 403
    old_token = alice.cookies.get(COOKIE)
    assert alice.post("/auth/logout").status_code == 204
    anonymous.cookies.set(COOKIE, old_token)
    assert anonymous.get("/api/session").status_code == 401
    assert bob.get("/api/session").status_code == 200


def test_dashboard_isolates_shared_card_state_and_private_email(hosted):
    engine, _, (owner, _), (alice, bob) = hosted
    nonce = str(uuid4())
    connection = uuid4()
    with engine.begin() as conn:
        public_id = conn.scalar(
            text("""
            INSERT INTO opportunity (uid, kind, title, company, company_norm, title_norm,
              is_remote, primary_origin, status, first_seen_at, last_seen_at)
            VALUES (:uid, 'INTERNSHIP', 'ML Intern', 'Test Inc', 'test', 'ml intern',
              false, 'ATS', 'ACTIVE', now(), now()) RETURNING id
        """),
            {"uid": nonce},
        )
    try:
        with owner_session(engine, owner) as session:
            params = {"owner": owner, "conn": connection, "public": public_id}
            session.execute(
                text("""
                INSERT INTO mail_connection (owner_user_id,id,provider,external_id)
                VALUES (:owner,:conn,'gmail','college')
            """),
                params,
            )
            session.execute(
                text("""
                INSERT INTO mail_delivery (owner_user_id, connection_id, delivery_id, received_at)
                VALUES (:owner,:conn,'id',now())
            """),
                params,
            )
            private_id = (
                -session.execute(
                    text("""
                INSERT INTO private_opportunity (owner_user_id,connection_id,delivery_id,
                    position,fields,public_opportunity_id)
                VALUES (:owner,:conn,'id',0,'{"role":"ML Intern","company":"Test Inc",
                    "deadline":"2099-01-01"}',:public),
                    (:owner,:conn,'id',1,'{"role":"Private Intern","company":"Only Alice"}',null)
                RETURNING id
            """),
                    params,
                )
                .scalars()
                .all()[1]
            )
        path = f"/api/opportunities/{public_id}"
        assert alice.get(path).json()["deadline_at"] is not None
        assert bob.get(path).json()["deadline_at"] is None
        assert alice.get(f"/api/opportunities/{private_id}").status_code == 200
        assert bob.get(f"/api/opportunities/{private_id}").status_code == 404
        assert bob.patch(f"/api/opportunities/{private_id}", json={"seen": True}).status_code == 404
        assert (
            alice.patch(path, json={"notes": "mine", "bookmarked": True}).json()["notes"] == "mine"
        )
        assert bob.get(path).json()["notes"] is None
        assert alice.get("/api/overview").json()["saved"] == 1
        assert bob.get("/api/overview").json()["saved"] == 0
        assert alice.get("/api/overview").json()["inboxes"]
        assert bob.get("/api/overview").json()["inboxes"] == {}
        assert alice.post("/api/opportunities/mark-seen").status_code == 200
        assert bob.get(path).json()["seen"] is False
        assert bob.get("/api/funding").status_code == 200
        assert alice.get("/api/sources").json() == bob.get("/api/sources").json()
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM opportunity WHERE id = :id"), {"id": public_id})


def test_sign_in_replaces_previous_session(hosted):
    from fastapi import HTTPException

    engine, _, owners, (alice, _) = hosted
    old = alice.cookies.get(COOKIE)
    with owner_session(engine, owners[0]) as session:
        subject = session.execute(text("SELECT identity_subject FROM account")).scalar_one()
    new = create_session(engine, ISSUER, subject, old)
    assert principal_for(engine, new).id == owners[0]
    with pytest.raises(HTTPException) as exc:
        principal_for(engine, old)
    assert exc.value.status_code == 401
