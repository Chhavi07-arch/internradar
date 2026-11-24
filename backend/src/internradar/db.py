from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from internradar import models  # noqa: F401  (registers tables on SQLModel.metadata)
from internradar.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")  # dashboard can read while a fetch writes
        cur.close()

    return engine


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    if engine.dialect.name != "sqlite":
        raise RuntimeError("The current app supports SQLite only; migrate PostgreSQL with Alembic")
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Additive schema upgrades: new nullable columns appear without losing local data.

    create_all() only creates missing tables. Anything beyond adding a nullable column
    (renames, type changes) needs a real migration tool.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"{table.name}.{column.name} is new and NOT NULL; add a migration"
                    )
                kind = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'))


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    with Session(engine or get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
