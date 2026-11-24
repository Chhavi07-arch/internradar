import sqlite3
from contextlib import closing

from internradar.legacy_audit import LegacyInventory, audit_legacy


def test_mixed_listing_is_not_a_public_candidate_even_with_public_primary_origin(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE opportunity (id INTEGER, primary_origin TEXT, notes TEXT);
            CREATE TABLE sighting (opportunity_id INTEGER, origin TEXT, source TEXT);
            CREATE TABLE emailmessage (body TEXT);
            INSERT INTO opportunity VALUES (1, 'BOARD', NULL), (2, 'BOARD', 'private note'),
                (3, 'EMAIL', NULL), (4, 'BOARD', NULL);
            INSERT INTO sighting VALUES (1, 'BOARD', 'board'), (2, 'BOARD', 'board'),
                (2, 'EMAIL', 'gmail:college'), (3, 'EMAIL', 'gmail:college');
            INSERT INTO emailmessage VALUES ('private body');
        """)
    before = path.read_bytes()
    expected = LegacyInventory(1, 3, 1, 1)
    assert audit_legacy(path) == audit_legacy(path) == expected
    assert path.read_bytes() == before
