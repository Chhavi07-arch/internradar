import os

from alembic import context
from sqlalchemy import create_engine


def run() -> None:
    url = os.environ["INTERNRADAR_MIGRATION_DATABASE_URL"]
    engine = create_engine(url)
    try:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("Phase A migrations require PostgreSQL")
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
