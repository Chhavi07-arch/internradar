import pytest
from sqlmodel import Session, SQLModel, create_engine

from internradar.config import Candidate, Locations, SearchConfig


@pytest.fixture
def search() -> SearchConfig:
    return SearchConfig(
        candidate=Candidate(batch=2028, degree="B.Tech CSE"),
        roles=["machine learning intern"],
        locations=Locations(cities=["Bengaluru"], remote=True),
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
