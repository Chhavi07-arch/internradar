import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from internradar.legacy_import import LegacyImportError, import_legacy
from internradar.ownership import owner_session


@pytest.fixture
def backup(tmp_path):
    path = tmp_path / "backup.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE opportunity (id INTEGER PRIMARY KEY, uid TEXT, company TEXT,
                title TEXT, seen_at TEXT, bookmarked INTEGER, hidden INTEGER,
                notes TEXT, first_seen_at TEXT, description TEXT, url TEXT);
            CREATE TABLE sighting (opportunity_id INTEGER, origin TEXT,
                source TEXT, native_id TEXT);
            CREATE TABLE emailmessage (account TEXT, gmail_id TEXT, kind TEXT, received_at TEXT,
                extracted_at TEXT, extracted TEXT, body TEXT, subject TEXT, sender TEXT);
            CREATE TABLE credentials (secret TEXT);
            INSERT INTO credentials VALUES ('DO NOT COPY TOKEN');
            INSERT INTO opportunity VALUES
                (1, 'mixed', 'private company', 'private role', '2025-01-01 12:34:56', 1, 0,
                    'private note', '2025-01-01', 'DO NOT COPY BODY', 'https://private.test/'),
                (2, 'mail', 'school', 'drive', NULL, 0, 1, 'drive note', '2025-01-01', NULL, NULL),
                (3, 'unmatched', 'Unknown', 'archived role', NULL, 1, 0, 'orphan note',
                    '2025-01-01', 'DO NOT COPY BODY', 'https://private.test/'),
                (4, 'second-mixed', 'private', 'private', NULL, 1, 0, 'do not overwrite',
                    '2025-01-01', NULL, NULL),
                (5, 'ambiguous', 'private', 'ambiguous role', NULL, 1, 0, 'ambiguous note',
                    '2025-01-01', NULL, NULL);
            INSERT INTO sighting VALUES
                (1, 'EMAIL', 'gmail:college', 'message#0'), (1, 'ATS', 'greenhouse:test', 'A'),
                (2, 'EMAIL', 'gmail:college', 'message#1'),
                (4, 'ATS', 'greenhouse:test', 'B'),
                (5, 'ATS', 'greenhouse:test', 'A'), (5, 'ATS', 'greenhouse:test', 'B');
            INSERT INTO emailmessage VALUES ('college', 'pending', 'OPPORTUNITY', '2025-01-01',
                NULL, NULL, 'DO NOT COPY BODY', 'DO NOT COPY SUBJECT', 'DO NOT COPY SENDER');
        """)
        db.execute(
            "INSERT INTO emailmessage VALUES ('college', 'message', 'OPPORTUNITY', '2025-01-01',"
            "'2025-01-02', ?, 'DO NOT COPY BODY', 'DO NOT COPY SUBJECT', 'DO NOT COPY SENDER')",
            (
                json.dumps(
                    [
                        {
                            "company": "School",
                            "role": "Role A",
                            "body": "DO NOT COPY BODY",
                            "eligibility": {"branches": ["CSE"], "other": "DO NOT COPY TOKEN"},
                            "apply_url": "https://jobs.example/role?token=private#fragment",
                        },
                        {"company": "School", "role": "Role B", "token": "DO NOT COPY TOKEN"},
                    ]
                ),
            ),
        )
        db.commit()
    return path


@pytest.fixture
def pg():
    # These URLs must point to one fresh, disposable migrated database.
    admin_url = os.getenv("INTERNRADAR_TEST_LEGACY_ADMIN_URL")
    runtime_url = os.getenv("INTERNRADAR_TEST_LEGACY_RUNTIME_URL")
    if not admin_url or not runtime_url:
        pytest.skip("requires isolated migrated PostgreSQL with separate admin and runtime roles")
    admin, runtime = create_engine(admin_url), create_engine(runtime_url)
    owner, other = uuid4(), uuid4()
    with admin.connect() as db:
        assert db.execute(text("SELECT current_database()")).scalar_one() == "ir_legacy_import_test"
    try:
        with admin.begin() as db:
            ids = []
            for suffix in ("A", "B"):
                ids.append(
                    db.execute(
                        text("""
                    INSERT INTO opportunity (uid, kind, title, company, company_norm, title_norm,
                        is_remote, description, primary_origin, status, first_seen_at, last_seen_at)
                    VALUES (:uid, 'INTERNSHIP', 'Clean Role', 'Clean', 'clean', 'clean role',
                        false, 'clean public description', 'ATS', 'ACTIVE', now(), now())
                    RETURNING id
                    """),
                        {"uid": f"{owner}-{suffix}"},
                    ).scalar_one()
                )
                db.execute(
                    text("""
                    INSERT INTO sighting (opportunity_id, source, origin, native_id, first_seen_at,
                        last_seen_at, missed_runs)
                    VALUES (:id, 'greenhouse:test', 'ATS', :suffix, now(), now(), 0)
                    """),
                    {"id": ids[-1], "suffix": suffix},
                )
        for account in (owner, other):
            with owner_session(admin, account) as db:
                db.execute(
                    text("""
                    INSERT INTO account (id, identity_issuer, identity_subject)
                    VALUES (:id, 'test', :subject)
                    """),
                    {"id": account, "subject": str(account)},
                )
        with owner_session(admin, owner) as db:
            db.execute(
                text("""
                INSERT INTO user_opportunity (owner_user_id, opportunity_id, notes)
                VALUES (:owner, :id, 'destination wins')
                """),
                {"owner": owner, "id": ids[1]},
            )
        with runtime.connect() as db:
            assert db.execute(text("SELECT current_database()")).scalar_one() == (
                "ir_legacy_import_test"
            )
            assert db.execute(
                text("""
                SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname = current_user
                """)
            ).scalar_one()
        yield runtime, owner, other, ids
    finally:
        with admin.begin() as db:
            db.execute(
                text("DELETE FROM account WHERE id IN (:owner, :other)"),
                {"owner": owner, "other": other},
            )
            db.execute(
                text(
                    "DELETE FROM sighting WHERE source = 'greenhouse:test' AND "
                    "opportunity_id IN (SELECT id FROM opportunity WHERE uid LIKE :prefix)"
                ),
                {"prefix": f"{owner}-%"},
            )
            db.execute(
                text("DELETE FROM opportunity WHERE uid LIKE :prefix"), {"prefix": f"{owner}-%"}
            )
        admin.dispose()
        runtime.dispose()


def test_import_preview_apply_rerun_owner_isolation_and_no_raw_copy(backup, pg):
    engine, owner, other, ids = pg
    before = backup.read_bytes()
    dry = import_legacy(backup, engine, owner_id=owner)
    assert dry.dry_run
    assert (dry.public_state, dry.mail_postings, dry.state_on_mail, dry.archived_state) == (
        1,
        2,
        1,
        2,
    )
    assert (dry.mail_deliveries, dry.existing) == (3, 1)
    with owner_session(engine, owner) as db:
        assert db.execute(text("SELECT count(*) FROM mail_connection")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM user_opportunity")).scalar_one() == 1
    applied = import_legacy(backup, engine, owner_id=owner, dry_run=False)
    dry.dry_run = False
    assert applied == dry
    rerun = import_legacy(backup, engine, owner_id=owner, dry_run=False)
    assert (
        rerun.public_state,
        rerun.mail_postings,
        rerun.archived_state,
        rerun.mail_deliveries,
    ) == (0, 0, 0, 0)
    assert backup.read_bytes() == before
    with owner_session(engine, owner) as db:
        assert db.execute(
            text("""
            SELECT opportunity_id, seen_at, bookmarked, notes FROM user_opportunity
            ORDER BY opportunity_id
            """)
        ).all() == [
            (ids[0], datetime(2025, 1, 1, 12, 34, 56, tzinfo=UTC), True, "private note"),
            (ids[1], None, False, "destination wins"),
        ]
        assert (
            db.execute(
                text("""
            SELECT count(*) FROM mail_connection WHERE disabled_at IS NOT NULL
            AND encrypted_credentials IS NULL AND key_version IS NULL
            """)
            ).scalar_one()
            == 1
        )
        assert (
            db.execute(
                text("""
            SELECT count(*) FROM mail_delivery WHERE processed_at IS NOT NULL
            AND raw_ciphertext IS NULL AND raw_expires_at IS NULL AND raw_key_version IS NULL
            """)
            ).scalar_one()
            == 3
        )
        postings = db.execute(
            text("""
            SELECT fields, hidden, notes, public_opportunity_id FROM private_opportunity
            """)
        ).all()
        assert len(postings) == 4
        assert {row.notes for row in postings} == {
            None,
            "drive note",
            "orphan note",
            "ambiguous note",
        }
        assert next(row for row in postings if row.notes == "drive note").hidden
        assert all(row.public_opportunity_id is None for row in postings)
        assert "DO NOT COPY" not in repr(postings)
        assert (
            next(row.fields for row in postings if row.fields["role"] == "Role A")["apply_url"]
            == "https://jobs.example/role"
        )
        assert (
            db.execute(
                text("""
            SELECT description FROM opportunity WHERE id IN (:a, :b) ORDER BY id
            """),
                {"a": ids[0], "b": ids[1]},
            )
            .scalars()
            .all()
            == ["clean public description"] * 2
        )
    with owner_session(engine, other) as db:
        for table in (
            "user_opportunity",
            "mail_connection",
            "mail_delivery",
            "private_opportunity",
        ):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_refuses_unknown_owner_and_rolls_back_on_malformed_extraction(backup, pg):
    engine, owner, _, _ = pg
    with pytest.raises(LegacyImportError, match="owner account does not exist"):
        import_legacy(backup, engine, owner_id=uuid4(), dry_run=False)
    with closing(sqlite3.connect(backup)) as db, db:
        db.execute("UPDATE emailmessage SET extracted = '[null]' WHERE gmail_id = 'message'")
    with pytest.raises(LegacyImportError, match="invalid legacy mail extraction"):
        import_legacy(backup, engine, owner_id=owner, dry_run=False)
    with owner_session(engine, owner) as db:
        assert db.execute(text("SELECT count(*) FROM user_opportunity")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM mail_connection")).scalar_one() == 0


def test_requires_explicit_owner_and_offline_backup(backup):
    engine = create_engine("sqlite://")
    with pytest.raises(LegacyImportError, match=r"explicit.*owner"):
        import_legacy(backup, engine, owner_id=None)
    backup.with_name(f"{backup.name}-wal").touch()
    with pytest.raises(LegacyImportError, match="without journal sidecars"):
        import_legacy(backup, engine, owner_id=uuid4())
    engine.dispose()
    cli = subprocess.run(
        [sys.executable, "-m", "internradar.legacy_import", str(backup)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 2
    assert "required: --owner" in cli.stderr
