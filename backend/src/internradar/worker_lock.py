"""Cross-process coordination for catalogue refreshes.

The scheduler claims each named job separately. This lock additionally serializes public
catalogue writes across job types and manual runs, including work that outlives a claim lease.
The caller must keep the context open for the entire protected operation.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

# Stable application namespace and job ID; independent of Python's hash seed.
_CATALOGUE_REFRESH = {"namespace": 0x49524452, "job": 1}


@contextmanager
def catalogue_refresh_lock(engine: Engine) -> Iterator[Connection | None]:
    if engine.dialect.name != "postgresql":
        raise ValueError("cross-process coordination requires PostgreSQL")
    # Transaction-level advisory lock: rollback always releases it, and there is no
    # session-level lock left behind if the connection returns to a pool.
    with engine.connect() as connection, connection.begin():
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace, :job)"), _CATALOGUE_REFRESH
        )
        yield connection if acquired else None
