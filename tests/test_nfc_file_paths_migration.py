"""Migration tests for NFC file paths and NFC/NFD twin merging (#1275)."""

import hashlib
import sqlite3
import sys
import unicodedata
from importlib import import_module

import pytest
from alembic import command

from tests.test_note_content_migration import sqlite_alembic_config

migration = import_module("basic_memory.alembic.versions.0042_nfc_file_paths")

DOWN_REVISION: str = str(migration.down_revision)

NAME = "Заметка Saarbrücken й.md"
NFC = unicodedata.normalize("NFC", NAME)
NFD = unicodedata.normalize("NFD", NAME)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Off macOS stored paths stay byte-exact and the migration does nothing",
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A SQLite database at the revision before 0042, with one project."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "nfc.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, DOWN_REVISION)
    project_root = tmp_path / "project"
    project_root.mkdir()
    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO project (id, external_id, name, permalink, path, is_active, "
        "created_at, updated_at) "
        "VALUES (1, 'external-1', 'main', 'main', ?, 1, "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        (str(project_root),),
    )
    connection.commit()

    def upgrade() -> None:
        connection.commit()
        command.upgrade(config, "head")

    yield connection, upgrade, project_root
    connection.close()


def _insert_entity(
    connection: sqlite3.Connection, entity_id: int, permalink: str, file_path: str
) -> None:
    connection.execute(
        "INSERT INTO entity (id, external_id, title, note_type, content_type, project_id, "
        "permalink, file_path, checksum, created_at, updated_at) "
        "VALUES (?, ?, 'Note', 'note', 'text/markdown', 1, ?, ?, 'abc', "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        (entity_id, f"entity-{entity_id}", permalink, file_path),
    )


def _insert_relation(connection: sqlite3.Connection, from_id: int, to_id: int, to_name: str):
    connection.execute(
        "INSERT INTO relation (from_id, to_id, to_name, relation_type, project_id) "
        "VALUES (?, ?, ?, 'links_to', 1)",
        (from_id, to_id, to_name),
    )


def _insert_note_content(
    connection: sqlite3.Connection,
    entity_id: int,
    file_path: str,
    body: str,
    updated_at: str,
    status: str = "pending",
) -> None:
    connection.execute(
        "INSERT INTO note_content (entity_id, project_id, external_id, file_path, "
        "markdown_content, db_version, db_checksum, file_write_status, updated_at) "
        "VALUES (?, 1, ?, ?, ?, 1, 'sum', ?, ?)",
        (entity_id, f"entity-{entity_id}", file_path, body, status, updated_at),
    )


def test_upgrade_merges_twins_and_normalizes_paths(database):
    connection, upgrade, project_root = database
    _insert_entity(connection, 1, "notes/zametka", f"notes/{NFC}")
    _insert_entity(connection, 2, "notes/zametka-1", f"notes/{NFD}")
    _insert_entity(connection, 3, "notes/other", "notes/other.md")
    _insert_entity(connection, 4, "notes/solo", f"solo/{NFD}")
    connection.execute(
        "INSERT INTO observation (entity_id, project_id, content, category) "
        "VALUES (2, 1, 'twin observation', 'note')"
    )
    _insert_relation(connection, 3, 2, "notes/zametka-1")

    upgrade()

    assert connection.execute(
        "SELECT id, permalink, file_path, checksum FROM entity ORDER BY id"
    ).fetchall() == [
        (1, "notes/zametka", f"notes/{NFC}", None),
        (3, "notes/other", "notes/other.md", "abc"),
        (4, "notes/solo", f"solo/{NFC}", "abc"),
    ]
    assert connection.execute(
        "SELECT count(*) FROM observation WHERE entity_id = 2"
    ).fetchone() == (0,)
    assert connection.execute("SELECT from_id, to_id FROM relation").fetchall() == [(3, 1)]


def test_upgrade_keeps_a_twins_unwritten_content(database):
    """An accepted write held only by the twin must survive the merge."""
    connection, upgrade, project_root = database
    _insert_entity(connection, 1, "notes/zametka", f"notes/{NFC}")
    _insert_entity(connection, 2, "notes/zametka-1", f"notes/{NFD}")
    _insert_note_content(connection, 2, f"notes/{NFD}", "accepted edit", "2026-02-01 00:00:00")

    upgrade()

    # external_id must follow the row to the keeper; reads look content up by it.
    assert connection.execute(
        "SELECT entity_id, external_id, file_path, markdown_content FROM note_content"
    ).fetchall() == [(1, "entity-1", f"notes/{NFC}", "accepted edit")]


def test_upgrade_prefers_unwritten_content_over_a_fresher_synced_copy(database):
    """Reconciliation refreshes updated_at on unchanged rows, so it is no guide."""
    connection, upgrade, project_root = database
    _insert_entity(connection, 1, "notes/zametka", f"notes/{NFC}")
    _insert_entity(connection, 2, "notes/zametka-1", f"notes/{NFD}")
    _insert_note_content(connection, 1, f"notes/{NFC}", "unwritten edit", "2026-01-01 00:00:00")
    _insert_note_content(
        connection, 2, f"notes/{NFD}", "old disk copy", "2026-03-01 00:00:00", status="synced"
    )

    upgrade()

    assert connection.execute(
        "SELECT entity_id, markdown_content FROM note_content"
    ).fetchall() == [(1, "unwritten edit")]


def test_upgrade_stops_when_two_twins_hold_unwritten_content(database):
    """Two accepted writes for one file cannot both survive; refuse instead of guessing."""
    connection, upgrade, project_root = database
    _insert_entity(connection, 1, "notes/zametka", f"notes/{NFC}")
    _insert_entity(connection, 2, "notes/zametka-1", f"notes/{NFD}")
    _insert_note_content(connection, 1, f"notes/{NFC}", "edit one", "2026-01-01 00:00:00")
    _insert_note_content(connection, 2, f"notes/{NFD}", "edit two", "2026-01-02 00:00:00")

    with pytest.raises(Exception, match="accepted write"):
        upgrade()


def test_upgrade_drops_an_edge_that_would_duplicate_one_into_the_kept_row(database):
    """A note linking to both twins keeps one edge instead of aborting the upgrade."""
    connection, upgrade, project_root = database
    _insert_entity(connection, 1, "notes/zametka", f"notes/{NFC}")
    _insert_entity(connection, 2, "notes/zametka-1", f"notes/{NFD}")
    _insert_entity(connection, 3, "notes/other", "notes/other.md")
    _insert_relation(connection, 3, 1, "notes/zametka")
    _insert_relation(connection, 3, 2, "notes/zametka-1")

    upgrade()

    assert connection.execute("SELECT from_id, to_id, to_name FROM relation").fetchall() == [
        (3, 1, "notes/zametka")
    ]


def _insert_vacate(connection: sqlite3.Connection, marker_id: int, path: str, checksum: str):
    connection.execute(
        "INSERT INTO note_file_vacate (id, project_id, file_path, file_checksum, created_at) "
        "VALUES (?, 1, ?, ?, '2026-01-01 00:00:00')",
        (marker_id, path, checksum),
    )


def test_upgrade_keeps_the_vacate_marker_matching_the_file_on_disk(database):
    """A refreshed marker keeps its lower id, so the id cannot pick the current one."""
    connection, upgrade, project_root = database
    (project_root / "old").mkdir()
    content = b"# still on disk\n"
    (project_root / "old" / NFC).write_bytes(content)
    _insert_vacate(connection, 1, f"old/{NFC}", hashlib.sha256(content).hexdigest())
    _insert_vacate(connection, 2, f"old/{NFD}", "stale")

    upgrade()

    assert connection.execute(
        "SELECT id, file_path, file_checksum FROM note_file_vacate"
    ).fetchall() == [(1, f"old/{NFC}", hashlib.sha256(content).hexdigest())]


def test_upgrade_keeps_the_lowest_marker_when_no_guard_matches(database):
    connection, upgrade, project_root = database
    _insert_vacate(connection, 1, f"old/{NFC}", "first")
    _insert_vacate(connection, 2, f"old/{NFD}", "second")

    upgrade()

    assert connection.execute(
        "SELECT id, file_path, file_checksum FROM note_file_vacate"
    ).fetchall() == [(1, f"old/{NFC}", "first")]
