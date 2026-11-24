"""Read-only inventory before moving a legacy SQLite catalogue into PostgreSQL."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LegacyInventory:
    public_candidates: int
    private_or_unproven: int
    private_or_unproven_with_user_state: int
    stored_mail: int


def audit_legacy(path: Path) -> LegacyInventory:
    """Report counts only. No fields are copied and no target database is opened.

    Classification is deliberately conservative: a public primary origin does not
    prove the row was never enriched by a private sighting.
    """
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        columns = {row[1] for row in db.execute("PRAGMA table_info(opportunity)")}
        has_state = [
            expr
            for name, expr in (
                ("seen_at", "o.seen_at IS NOT NULL"),
                ("bookmarked", "o.bookmarked != 0"),
                ("hidden", "o.hidden != 0"),
                ("notes", "length(trim(o.notes)) > 0"),
            )
            if name in columns
        ]
        state = " OR ".join(has_state) or "0"
        counts = db.execute(f"""
            WITH classified AS (
                SELECT o.primary_origin IN ('ATS', 'LIST', 'BOARD')
                    AND EXISTS (SELECT 1 FROM sighting s WHERE s.opportunity_id = o.id)
                    AND NOT EXISTS (
                        SELECT 1 FROM sighting s WHERE s.opportunity_id = o.id
                        AND (s.origin NOT IN ('ATS', 'LIST', 'BOARD')
                             OR lower(s.source) LIKE 'gmail:%')
                    ) AS public_candidate,
                    ({state}) AS has_state
                FROM opportunity o
            )
            SELECT count(*) FILTER (WHERE public_candidate),
                   count(*) FILTER (WHERE NOT public_candidate),
                   count(*) FILTER (WHERE NOT public_candidate AND has_state)
            FROM classified
        """).fetchone()
        has_mail = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'emailmessage'"
        ).fetchone()
        mail = db.execute("SELECT count(*) FROM emailmessage").fetchone()[0] if has_mail else 0
    return LegacyInventory(*counts, mail)
