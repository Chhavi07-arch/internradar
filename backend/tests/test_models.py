import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from internradar.models import Opportunity, Origin, Sighting


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


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
