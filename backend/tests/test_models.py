import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from internradar.models import Opportunity, Origin, Sighting


def _opportunity(uid: str = "abc") -> Opportunity:
    return Opportunity(
        uid=uid,
        title="ML Intern",
        company="Acme",
        company_norm="acme",
        title_norm="machine learning intern",
        primary_origin=Origin.BOARD,
    )


def test_one_opportunity_many_sightings(session):
    opp = _opportunity()
    session.add(opp)
    session.flush()
    session.add(
        Sighting(opportunity_id=opp.id, source="linkedin", origin=Origin.BOARD, native_id="1")
    )
    session.add(
        Sighting(opportunity_id=opp.id, source="naukri", origin=Origin.BOARD, native_id="1")
    )
    session.commit()


def test_same_native_id_from_same_source_is_rejected(session):
    opp = _opportunity()
    session.add(opp)
    session.flush()
    session.add(
        Sighting(opportunity_id=opp.id, source="linkedin", origin=Origin.BOARD, native_id="1")
    )
    session.add(
        Sighting(opportunity_id=opp.id, source="linkedin", origin=Origin.BOARD, native_id="1")
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_origin_rank_prefers_company_boards():
    assert Origin.ATS.rank < Origin.LIST.rank < Origin.BOARD.rank < Origin.EMAIL.rank


def test_init_db_adds_new_nullable_columns_without_losing_rows(tmp_path):
    from sqlalchemy import inspect, text

    from internradar.db import init_db

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(_opportunity("kept"))
        s.commit()
    with engine.begin() as conn:  # simulate a database from before `eligibility` existed
        conn.execute(text("ALTER TABLE opportunity DROP COLUMN eligibility"))

    init_db(engine)
    assert "eligibility" in {c["name"] for c in inspect(engine).get_columns("opportunity")}
    with Session(engine) as s:
        assert s.exec(select(Opportunity)).one().uid == "kept"
