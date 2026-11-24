"""Transaction-scoped ownership for the PostgreSQL data model."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, text
from sqlmodel import Session


@contextmanager
def owner_session(engine: Engine, owner_id: UUID) -> Iterator[Session]:
    """Use a verified server-side identity, never an ID supplied in a request body.

    The setting is scoped to this transaction; policies deny access after it ends.
    Callers flush within the scope and leave commit/rollback to this context manager.
    """
    if engine.dialect.name != "postgresql":
        raise ValueError("multi-user ownership requires PostgreSQL")
    with Session(engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('internradar.owner_id', :owner, true)"),
            {"owner": str(owner_id)},
        )
        yield session
