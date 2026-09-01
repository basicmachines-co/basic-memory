"""Migration coverage for the memory_time_index table (SPEC-82).

The migration is deliberately dialect-neutral: every type it uses renders on SQLite and
PostgreSQL alike, so there is no branch to test per backend. What must be proven is
that the *same* definition arrives intact on both -- the columns, the cascade, the
lookup index, and the three CHECK constraints that keep an impossible range out of the
projection in the first place.

Two halves, following the repo's established split: a real SQLite upgrade/downgrade
round trip, and an offline render of the same migration against the PostgreSQL dialect.
"""

import io
import sqlite3
from importlib import import_module
from typing import Any

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_note_content_migration import sqlite_alembic_config

migration = import_module("basic_memory.alembic.versions.u4t5e6m7p8o9_add_memory_time_index_table")

# Pin the downgrade target to this migration's own parent. A relative "-1" would instead
# undo whichever migration currently sits at head, so the test would break every time a
# later revision lands.
DOWN_REVISION: str = str(migration.down_revision)

EXPECTED_COLUMNS = {
    "id",
    "project_id",
    "entity_id",
    "source_type",
    "source_id",
    "time_kind",
    "range_axis",
    "lower_value",
    "upper_value",
    "lower_inclusive",
    "upper_inclusive",
    "is_empty",
    "extractor",
    "source_text",
    "assertion_metadata",
}

# One row per column, in the table's declared order, for the constraint probes below.
VALID_ROW = (
    1,  # project_id
    1,  # entity_id
    "observation",
    1,  # source_id
    "effective",
    "date",
    "2026-06-10",
    "2026-07-27",
    1,  # lower_inclusive
    0,  # upper_inclusive
    0,  # is_empty
    "observation",
    "@effective[2026-06-10,2026-07-27)",
)
INSERT_SQL = """
    INSERT INTO memory_time_index (
        project_id, entity_id, source_type, source_id, time_kind, range_axis,
        lower_value, upper_value, lower_inclusive, upper_inclusive, is_empty,
        extractor, source_text
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _upgraded_database(tmp_path, monkeypatch, name: str):
    """Run Alembic to head against a fresh temporary SQLite database."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / name
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")
    return database_path, config


def _row_with(**overrides: Any) -> tuple[Any, ...]:
    """One valid row with named columns replaced, for the constraint probes."""
    columns = [
        "project_id",
        "entity_id",
        "source_type",
        "source_id",
        "time_kind",
        "range_axis",
        "lower_value",
        "upper_value",
        "lower_inclusive",
        "upper_inclusive",
        "is_empty",
        "extractor",
        "source_text",
    ]
    values = dict(zip(columns, VALID_ROW))
    values.update(overrides)
    return tuple(values[column] for column in columns)


def _seed_parent_rows(connection: sqlite3.Connection) -> None:
    """Insert the project and entity the projection rows below hang off."""
    connection.execute(
        "INSERT INTO project (id, external_id, name, permalink, path, is_active,"
        " created_at, updated_at)"
        " VALUES (1, 'project-1', 'p', 'p', '/p', 1, '2026-01-01', '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO entity (id, external_id, project_id, title, note_type, permalink,"
        " file_path, content_type, created_at, updated_at)"
        " VALUES (1, 'entity-1', 1, 't', 'note', 'p/t', 't.md', 'text/markdown',"
        " '2026-01-01', '2026-01-01')"
    )


def test_alembic_upgrade_creates_memory_time_index_table(tmp_path, monkeypatch):
    """Upgrading to head creates the projection table with its full contract."""
    database_path, _ = _upgraded_database(tmp_path, monkeypatch, "memory-time-index.db")

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_time_index)").fetchall()
        }
        assert columns == EXPECTED_COLUMNS

        foreign_keys = connection.execute("PRAGMA foreign_key_list(memory_time_index)").fetchall()
        entity_fk = next(row for row in foreign_keys if row[3] == "entity_id")
        project_fk = next(row for row in foreign_keys if row[3] == "project_id")
        assert (entity_fk[2], entity_fk[4]) == ("entity", "id")
        # Valid time is removed with the entity it was asserted about.
        assert entity_fk[6].upper() == "CASCADE"
        assert (project_fk[2], project_fk[4]) == ("project", "id")
        # source_id addresses whichever table source_type names, so it carries no FK.
        assert {row[3] for row in foreign_keys} == {"entity_id", "project_id"}

        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(memory_time_index)").fetchall()
        }
        assert "ix_memory_time_index_lookup" in indexes
        assert "ix_memory_time_index_entity_id" in indexes

        lookup_columns = [
            row[2]
            for row in connection.execute(
                "PRAGMA index_info(ix_memory_time_index_lookup)"
            ).fetchall()
        ]
        # The predicate filters on project/kind/axis and projects (source_type, source_id),
        # so this one index both drives the scan and covers its output.
        assert lookup_columns == [
            "project_id",
            "time_kind",
            "range_axis",
            "source_type",
            "source_id",
        ]

        # Bound values are deliberately unindexed: the full-text candidate set drives.
        assert not any(index.startswith("ix_memory_time_index_lower") for index in indexes)
        assert not any(index.startswith("ix_memory_time_index_upper") for index in indexes)
    finally:
        connection.close()


def test_upgraded_table_accepts_a_well_formed_assertion(tmp_path, monkeypatch):
    """The CHECK constraints must not reject the rows the projection actually writes."""
    database_path, _ = _upgraded_database(tmp_path, monkeypatch, "memory-time-index-insert.db")

    connection = sqlite3.connect(database_path)
    try:
        _seed_parent_rows(connection)
        connection.execute(INSERT_SQL, VALID_ROW)
        # Unbounded and empty ranges are legal shapes, not edge cases.
        connection.execute(
            INSERT_SQL,
            _row_with(source_id=2, lower_value=None, lower_inclusive=0),
        )
        connection.execute(
            INSERT_SQL,
            _row_with(
                source_id=3,
                lower_value=None,
                upper_value=None,
                lower_inclusive=0,
                upper_inclusive=0,
                is_empty=1,
            ),
        )
        connection.commit()

        assert connection.execute("SELECT COUNT(*) FROM memory_time_index").fetchone()[0] == 3
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"range_axis": "week"}, "ck_memory_time_index_range_axis"),
        # An empty range with endpoints would describe the same interval two ways.
        ({"is_empty": 1}, "ck_memory_time_index_empty_has_no_bounds"),
        # PostgreSQL's rule: there is no endpoint to include on an unbounded side.
        (
            {"lower_value": None, "lower_inclusive": 1},
            "ck_memory_time_index_unbounded_is_exclusive",
        ),
    ],
    ids=["unknown-axis", "empty-with-bounds", "unbounded-but-inclusive"],
)
def test_check_constraints_reject_impossible_rows(tmp_path, monkeypatch, overrides, constraint):
    """An interval the domain cannot produce must not be storable either."""
    database_path, _ = _upgraded_database(
        tmp_path, monkeypatch, f"memory-time-index-{constraint}.db"
    )

    connection = sqlite3.connect(database_path)
    try:
        _seed_parent_rows(connection)
        with pytest.raises(sqlite3.IntegrityError, match=constraint):
            connection.execute(INSERT_SQL, _row_with(**overrides))
    finally:
        connection.close()


def test_alembic_downgrade_drops_memory_time_index_table(tmp_path, monkeypatch):
    """Downgrading past this revision removes the table and both of its indexes."""
    database_path, config = _upgraded_database(tmp_path, monkeypatch, "memory-time-index-down.db")
    command.downgrade(config, DOWN_REVISION)

    connection = sqlite3.connect(database_path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_time_index'"
        ).fetchone()
        remaining_indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
            " AND name LIKE 'ix_memory_time_index%'"
        ).fetchall()
    finally:
        connection.close()

    assert table_exists is None
    assert remaining_indexes == []


def test_postgres_render_carries_the_same_definition(monkeypatch):
    """The identical migration renders on PostgreSQL with no dialect branching.

    Rendering offline is what proves it: if any type, default, or constraint needed a
    backend-specific spelling, this would fail here rather than on a deploy.
    """
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()
    migration.downgrade()

    sql = buffer.getvalue()
    assert "CREATE TABLE memory_time_index" in sql
    assert "FOREIGN KEY(entity_id) REFERENCES entity (id) ON DELETE CASCADE" in sql
    assert "ck_memory_time_index_range_axis" in sql
    assert "ck_memory_time_index_empty_has_no_bounds" in sql
    assert "ck_memory_time_index_unbounded_is_exclusive" in sql
    assert (
        "CREATE INDEX ix_memory_time_index_lookup ON memory_time_index "
        "(project_id, time_kind, range_axis, source_type, source_id)" in sql
    )
    # Bounds stay portable text on both backends; a native range column would be a
    # later, generated addition rather than a change to this definition.
    assert "lower_value VARCHAR(32)" in sql
    assert "upper_value VARCHAR(32)" in sql
    assert "DROP TABLE memory_time_index" in sql
