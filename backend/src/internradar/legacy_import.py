"""Import a standalone legacy SQLite backup into an existing PostgreSQL owner account.

Rebuild the shared catalogue from public sources first. The importer writes only to
owner-scoped tables, never overwrites destination state, and defaults to a read-only
preview. See ``python -m internradar.legacy_import --help``.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from internradar.ownership import owner_session

_ARCHIVE = "internradar:legacy-sqlite"
_PUBLIC_ORIGINS = {"ATS", "LIST", "BOARD"}
_MAIL_KINDS = {"JOB_ALERT", "OPPORTUNITY"}


class LegacyImportError(ValueError):
    """The backup or destination did not meet an import precondition."""


@dataclass
class ImportReport:
    dry_run: bool
    public_state: int = 0
    mail_deliveries: int = 0
    mail_postings: int = 0
    state_on_mail: int = 0
    archived_state: int = 0
    existing: int = 0


def _key(prefix, *parts):
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()
    return f"{prefix}:{digest}"


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _state(row):
    if any(row[name] not in (None, 0, 1) for name in ("bookmarked", "hidden")):
        raise LegacyImportError("invalid legacy state flag")
    return {
        "seen_at": _timestamp(row["seen_at"]) if row["seen_at"] is not None else None,
        "bookmarked": bool(row["bookmarked"]),
        "hidden": bool(row["hidden"]),
        "notes": _string(row["notes"]),
    }


def _string(value, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or "\x00" in value or (required and not value.strip()):
        raise LegacyImportError("invalid text in an importable legacy field")
    return value


def _derived(item):
    """Rebuild the structured projection; never copy arbitrary JSON keys or metadata."""
    if not isinstance(item, dict):
        raise LegacyImportError("invalid legacy mail extraction")
    fields = {k: _string(item.get(k), required=True) for k in ("company", "role")}
    for name in ("location", "deadline", "stipend"):
        if name in item:
            fields[name] = _string(item[name])
    for name in ("is_internship", "is_remote"):
        if name in item:
            if not isinstance(item[name], bool):
                raise LegacyImportError("invalid boolean in legacy mail extraction")
            fields[name] = item[name]
    url = _string(item.get("apply_url"))
    if url:
        # Application links may contain tracking or per-recipient credentials.
        try:
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and parts.hostname and parts.username is None:
                fields["apply_url"] = parts._replace(query="", fragment="").geturl()
        except ValueError:
            pass  # Do not preserve malformed or credential-bearing URLs.
    if (eligibility := item.get("eligibility")) is not None:
        if not isinstance(eligibility, dict):
            raise LegacyImportError("invalid legacy mail eligibility")
        result = {"notes": _string(eligibility.get("notes"))}
        for name, item_type in (("branches", str), ("batches", int)):
            value = eligibility.get(name, [])
            if not isinstance(value, list) or any(type(x) is not item_type for x in value):
                raise LegacyImportError("invalid legacy mail eligibility list")
            result[name] = [_string(x) for x in value] if item_type is str else value
        cgpa = eligibility.get("min_cgpa")
        backlogs = eligibility.get("backlogs_allowed")
        if cgpa is not None and (type(cgpa) not in (int, float) or not math.isfinite(cgpa)):
            raise LegacyImportError("invalid legacy mail eligibility CGPA")
        if backlogs is not None and type(backlogs) is not bool:
            raise LegacyImportError("invalid legacy mail eligibility flag")
        fields["eligibility"] = result | {"min_cgpa": cgpa, "backlogs_allowed": backlogs}
    return fields


@contextmanager
def _backup(path):
    path = path.resolve(strict=True)
    if path.name == "internradar.db":
        raise LegacyImportError("name the standalone backup differently from the live database")
    if not path.is_file() or any(
        Path(f"{path}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")
    ):
        raise LegacyImportError("use a standalone SQLite backup without journal sidecars")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA trusted_schema = OFF")
        for table in ("opportunity", "sighting"):
            if not _has_table(db, table):
                raise LegacyImportError("missing a required legacy table")
        yield db


def _has_table(db, table):
    return (
        db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


class _Destination:
    def __init__(self, session, owner_id, report):
        self.session = session
        self.report = report
        self.params = {"owner": owner_id, "connection": uuid5(owner_id, _ARCHIVE)}
        self.archive_ready = False

    def execute(self, sql, **params):
        return self.session.execute(text(sql), self.params | params)

    def insert(self, table, columns, identity, *, counter=None, **params):
        # SQL identifiers and expressions are importer constants, never source data.
        where = " AND ".join(f"{key} = {columns[key]}" for key in identity)
        added = self.execute(f"SELECT 1 FROM {table} WHERE {where}", **params).first() is None
        if added and not self.report.dry_run:
            added = (
                self.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(columns.values())}) "
                    f"ON CONFLICT ({', '.join(identity)}) DO NOTHING",
                    **params,
                ).rowcount
                == 1
            )
        if counter:
            name = counter if added else "existing"
            setattr(self.report, name, getattr(self.report, name) + 1)
        return added

    def ensure_archive(self):
        if self.archive_ready:
            return
        columns = {
            "owner_user_id": ":owner",
            "id": ":connection",
            "provider": "'gmail'",
            "external_id": ":archive",
            "disabled_at": "now()",
        }
        added = self.insert("mail_connection", columns, ("owner_user_id", "id"), archive=_ARCHIVE)
        if (
            not added
            and not self.execute(
                """
            SELECT 1 FROM mail_connection WHERE owner_user_id = :owner AND id = :connection
            AND provider = 'gmail' AND external_id = :archive AND disabled_at IS NOT NULL
            AND encrypted_credentials IS NULL AND key_version IS NULL
            """,
                archive=_ARCHIVE,
            ).first()
        ):
            raise LegacyImportError(
                "existing archive connection is not a disabled credential-free archive"
            )
        self.archive_ready = True

    def public_match(self, sightings):
        matches = set()
        for sighting in sightings:
            source, origin, native = sighting
            if (
                origin in _PUBLIC_ORIGINS
                and isinstance(source, str)
                and source.strip()
                and source.strip().lower().split(":", 1)[0] not in {"gmail", "email"}
                and isinstance(native, str)
                and native
            ):
                match = self.execute(
                    """
                    SELECT opportunity_id FROM sighting WHERE source = :source
                    AND native_id = :native AND origin = :origin
                    """,
                    source=source,
                    native=native,
                    origin=origin,
                ).scalar_one_or_none()
                if match is not None:
                    matches.add(match)
        return next(iter(matches)) if len(matches) == 1 else None

    def public_state(self, opportunity, state):
        self.insert(
            "user_opportunity",
            {"owner_user_id": ":owner", "opportunity_id": ":opportunity", **_STATE_COLUMNS},
            ("owner_user_id", "opportunity_id"),
            counter="public_state",
            opportunity=opportunity,
            **state,
        )

    def delivery(self, delivery, received, processed, kind):
        self.ensure_archive()
        self.insert(
            "mail_delivery",
            _DELIVERY_COLUMNS
            | {
                "received_at": ":received",
                "processed_at": ":processed",
                "kind": ":kind",
            },
            tuple(_DELIVERY_COLUMNS),
            counter="mail_deliveries",
            delivery=delivery,
            received=received,
            processed=processed,
            kind=kind,
        )

    def posting(self, delivery, position, fields, state, *, counter):
        added = self.insert(
            "private_opportunity",
            _DELIVERY_COLUMNS
            | {
                "position": ":position",
                "fields": "CAST(:fields AS json)",
                **_STATE_COLUMNS,
            },
            (*_DELIVERY_COLUMNS, "position"),
            counter=counter,
            delivery=delivery,
            position=position,
            fields=json.dumps(fields, allow_nan=False),
            **state,
        )
        if added and counter == "mail_postings" and any(state.values()):
            self.report.state_on_mail += 1


_STATE_COLUMNS = {name: f":{name}" for name in ("seen_at", "bookmarked", "hidden", "notes")}
_EMPTY_STATE = {"seen_at": None, "bookmarked": False, "hidden": False, "notes": None}
_DELIVERY_COLUMNS = {
    "owner_user_id": ":owner",
    "connection_id": ":connection",
    "delivery_id": ":delivery",
}


def _mail_rows(db, target, mail_state):
    if not _has_table(db, "emailmessage"):
        return
    for row in db.execute("""
        SELECT account, gmail_id, kind, received_at, extracted_at, extracted
        FROM emailmessage WHERE extracted IS NOT NULL AND extracted_at IS NOT NULL
        ORDER BY account, gmail_id
    """):
        if row["kind"] not in _MAIL_KINDS:
            continue
        items = json.loads(row["extracted"])
        if items is None:
            continue
        if not isinstance(items, list):
            raise LegacyImportError("invalid legacy mail extraction list")
        label, message = (_string(row[k], required=True) for k in ("account", "gmail_id"))
        delivery = _key("mail", label, message)
        target.delivery(
            delivery,
            _timestamp(row["received_at"]),
            _timestamp(row["extracted_at"]),
            row["kind"].lower(),
        )
        for position, item in enumerate(items):
            state = mail_state.pop((f"gmail:{label}", f"{message}#{position}"), None)
            target.posting(
                delivery, position, _derived(item), state or _EMPTY_STATE, counter="mail_postings"
            )


def import_legacy(
    path: Path, engine: Engine, *, owner_id: UUID, dry_run: bool = True
) -> ImportReport:
    """Preview or atomically insert a single owner's backup, preserving existing target state."""
    if not isinstance(owner_id, UUID) or owner_id.int == 0:
        raise LegacyImportError("an explicit, nonzero destination owner UUID is required")
    report = ImportReport(dry_run)
    with _backup(path) as db, owner_session(engine, owner_id) as session:
        target = _Destination(session, owner_id, report)
        if dry_run:
            target.execute("SET TRANSACTION READ ONLY")
        else:
            target.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))",
                lock=f"{_ARCHIVE}:{owner_id}",
            )
        if target.execute("SELECT 1 FROM account WHERE id = :owner").first() is None:
            raise LegacyImportError("destination owner account does not exist")
        _import_rows(db, target)
    return report


def _import_rows(db, target):
    mail_state = {}
    fallback = []
    claimed_public = set()
    for row in db.execute("""
        SELECT id, uid, company, title, seen_at, bookmarked, hidden, notes, first_seen_at
        FROM opportunity WHERE seen_at IS NOT NULL OR bookmarked != 0 OR hidden != 0
        OR notes IS NOT NULL ORDER BY id
    """):
        state = _state(row)
        key = _key("state", _string(row["uid"], required=True))
        if target.execute(
            """
            SELECT 1 FROM private_opportunity WHERE owner_user_id = :owner
            AND connection_id = :connection AND delivery_id = :delivery AND position = 0
            """,
            delivery=key,
        ).first():
            target.report.existing += 1
            continue  # Do not recreate previously archived state on later public rebuilds.
        sightings = db.execute(
            """
            SELECT source, origin, native_id FROM sighting WHERE opportunity_id = ?
        """,
            (row["id"],),
        ).fetchall()
        public = target.public_match(sightings)
        if public is not None and public not in claimed_public:
            target.public_state(public, state)
            claimed_public.add(public)
            continue
        refs = [
            (source, native)
            for source, origin, native in sightings
            if origin == "EMAIL" and isinstance(source, str) and source.startswith("gmail:")
        ]
        ref = refs[0] if len(refs) == 1 and refs[0] not in mail_state else None
        if ref:
            mail_state[ref] = state
        fallback.append((key, row, state, ref))
    _mail_rows(db, target, mail_state)
    for key, row, state, ref in fallback:
        if ref is not None and ref not in mail_state:
            continue
        first_seen = _timestamp(row["first_seen_at"])
        fields = {
            "company": _string(row["company"], required=True),
            "role": _string(row["title"], required=True),
        }
        target.delivery(key, first_seen, first_seen, "legacy_state")
        target.posting(key, 0, fields, state, counter="archived_state")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Set INTERNRADAR_LEGACY_IMPORT_DATABASE_URL to an explicitly chosen, migrated "
            "PostgreSQL target (use a disposable database for rehearsal). Supply an offline "
            "SQLite backup, not the live dashboard file. Only use a backup whose state and "
            "mail all belong to the specified owner. Preserve the backup for later review. "
            "Use a restricted role with RLS, SELECT on account and sighting, SELECT/INSERT "
            "on the private import tables, and USAGE on private_opportunity_id_seq."
        ),
    )
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--owner", type=UUID, required=True, help="existing destination account UUID"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read-only preview (the default)")
    mode.add_argument("--apply", action="store_true", help="commit the import atomically")
    args = parser.parse_args()
    engine = None
    try:
        url = os.environ.get("INTERNRADAR_LEGACY_IMPORT_DATABASE_URL")
        if not url:
            raise LegacyImportError("set INTERNRADAR_LEGACY_IMPORT_DATABASE_URL explicitly")
        engine = create_engine(url, hide_parameters=True)
        report = import_legacy(args.backup, engine, owner_id=args.owner, dry_run=not args.apply)
    except LegacyImportError as exc:
        parser.exit(1, f"Import refused: {exc}.\n")
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, SQLAlchemyError):
        parser.exit(
            1, "Import failed; no changes committed. Check backup and target configuration.\n"
        )
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(asdict(report), sort_keys=True))


if __name__ == "__main__":
    main()
