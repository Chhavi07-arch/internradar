from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from internradar import api
from internradar.models import (
    FundingEvent,
    FundingMention,
    Kind,
    Lifecycle,
    Opportunity,
    Origin,
    Sighting,
    SourceRun,
    utcnow,
)


@pytest.fixture
def engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture
def client(engine):
    return TestClient(api.create_app(engine))


def add(engine, **overrides) -> int:
    now = utcnow()
    fields = dict(
        uid=str(overrides.get("title", "t")) + str(now.timestamp()), title="ML Intern",
        company="Acme AI", company_norm="acme ai", title_norm="machine learning intern",
        city="bengaluru", primary_origin=Origin.BOARD, first_seen_at=now, last_seen_at=now,
    )  # fmt: skip
    with Session(engine) as s:
        opportunity = Opportunity(**(fields | overrides))
        s.add(opportunity)
        s.flush()
        s.add(Sighting(opportunity_id=opportunity.id, source="linkedin", origin=Origin.BOARD,
                       native_id=str(opportunity.id)))  # fmt: skip
        s.commit()
        return opportunity.id


def test_inbox_lists_unseen_first_and_hides_closed_and_hidden(client, engine):
    add(engine, title="Old", first_seen_at=utcnow() - timedelta(days=3))
    new = add(engine, title="New")
    add(engine, title="Closed", status=Lifecycle.CLOSED)
    add(engine, title="Hidden", hidden=True)
    add(engine, title="Full-time", kind=Kind.JOB)
    client.patch(f"/api/opportunities/{new}", json={"seen": True})

    titles = [o["title"] for o in client.get("/api/opportunities").json()]
    assert titles == ["Old", "New"]  # unseen before seen, even though older
    assert [o["title"] for o in client.get("/api/opportunities?view=hidden").json()] == ["Hidden"]


def test_patch_updates_user_state_and_trims_notes(client, engine):
    oid = add(engine)
    body = client.patch(f"/api/opportunities/{oid}",
                        json={"bookmarked": True, "notes": "  ask senior  "}).json()  # fmt: skip
    assert body["bookmarked"] and body["notes"] == "ask senior"
    assert client.patch(f"/api/opportunities/{oid}", json={"notes": "  "}).json()["notes"] is None
    assert client.patch("/api/opportunities/999", json={"seen": True}).status_code == 404


def test_search_and_saved_and_deadline_views(client, engine):
    add(engine, title="Backend Intern", company="Zeta")
    saved = add(engine, title="ML Intern", bookmarked=True,
                deadline_at=utcnow() + timedelta(days=2))  # fmt: skip
    assert [o["company"] for o in client.get("/api/opportunities?q=zeta").json()] == ["Zeta"]
    assert [o["id"] for o in client.get("/api/opportunities?view=saved").json()] == [saved]
    assert [o["id"] for o in client.get("/api/opportunities?view=deadlines").json()] == [saved]
    assert client.get("/api/overview").json()["due_this_week"] == 1


def test_mark_all_seen(client, engine):
    add(engine)
    add(engine, title="B")
    assert client.post("/api/opportunities/mark-seen").json() == {"marked": 2}
    assert client.get("/api/overview").json()["unseen"] == 0


def test_detail_includes_sightings_and_funding_badge(client, engine):
    oid = add(engine)
    with Session(engine) as s:
        event = FundingEvent(company="Acme AI", company_norm="acme ai", round="Series A",
                             amount_text="$10M", is_tech=True, headline="h", url="u")  # fmt: skip
        s.add(event)
        s.flush()
        s.add(FundingMention(event_id=event.id, url="u", feed="inc42", headline="h"))
        s.commit()
    detail = client.get(f"/api/opportunities/{oid}").json()
    assert detail["sightings"][0]["source"] == "linkedin"
    assert detail["funding"].startswith("Series A $10M")
    [round_] = client.get("/api/funding").json()
    assert round_["internships"] == 1 and round_["mentions"] == 1


def test_source_health_uses_each_sources_latest_run(client, engine):
    with Session(engine) as s:
        s.add(SourceRun(run_id=1, source="linkedin", ok=False, exhaustive=False, error="429"))
        s.add(SourceRun(run_id=1, source="naukri", ok=True, exhaustive=False))
        s.add(SourceRun(run_id=2, source="linkedin", ok=True, exhaustive=False))  # partial run
        s.commit()
    health = {h["source"]: h["ok"] for h in client.get("/api/sources").json()}
    assert health == {"linkedin": True, "naukri": True}


def test_per_day_counts_by_origin(client, engine):
    add(engine, primary_origin=Origin.ATS)
    add(engine, title="B")
    today = client.get("/api/overview").json()["per_day"][-1]
    assert (today["ats"], today["board"]) == (1, 1)


def test_fetch_runs_in_background_and_reports_result(client, monkeypatch):
    class Report:
        new = 7

    monkeypatch.setattr(api, "fetch_everything", lambda *a, **k: Report())
    assert client.post("/api/fetch", json={}).status_code == 202
    for _ in range(50):
        state = client.get("/api/fetch").json()
        if state["last_new"] is not None:
            break
    assert state == {"running": False, "last_finished_at": state["last_finished_at"],
                     "last_new": 7, "last_error": None}  # fmt: skip


def test_concurrent_fetch_is_rejected(client):
    with api.FETCH_LOCK:
        assert client.post("/api/fetch", json={}).status_code == 409
