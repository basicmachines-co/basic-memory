"""Normalize stored file paths to Unicode NFC and merge NFC/NFD twin entities.

macOS filesystems resolve the NFC and NFD spellings of a name to one file but
report whichever form is on disk, and file-sync tools rewrite that form
(Syncthing keeps NFD names on macOS). Before #1275 the index keyed entities by
the raw path, so a vault could hold two rows for one file -- the second with a
`-1` permalink -- and notes whose stored key was NFD. Paths now enter the index
in NFC on macOS (`basic_memory.utils.nfc_path`); this migration brings existing
rows to the same form so the first index pass after upgrading does not see a
wave of moves.

For each group of entities whose paths are equal after NFC normalization, the
oldest row (lowest id, which holds the original permalink) is kept:

- an accepted write that has not reached disk yet (`note_content` not `synced`)
  moves to the kept row; two such writes for one file stop the upgrade instead
  of dropping one;
- references to a twin (`relation.to_id`, `note_file_vacate.entity_id`,
  `accepted_project_note_change.entity_id`) move to the kept row; a relation
  that would duplicate one the kept row already receives is dropped instead;
- rows that only describe the twin are deleted, and the kept row's checksums
  are cleared so the next index pass re-reads the file, the source of truth.

`note_file_vacate` keys markers by path, so NFC/NFD spellings of one vacated
path are merged to the marker whose checksum guard matches the file on disk. Embeddings of
deleted vector chunks are left to the semantic index's orphan sweep, because
removing them here would need the sqlite-vec extension inside the migration.

Linux and Windows filesystems keep NFC and NFD names as two distinct files, so
there stored paths stay byte-exact and this migration does nothing -- the same
rule `nfc_path` applies.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-14 12:00:00.000000

"""

import hashlib
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, columns the statement needs, statement), run in order for each twin.
# A statement runs only when its table and columns exist: search_index is derived
# state whose shape differs across older databases, and the next index pass
# rebuilds it anyway.
TWIN_STATEMENTS = (
    # The twin's outgoing edges describe the same file the kept row describes.
    ("relation", ("from_id",), "DELETE FROM relation WHERE from_id = :twin"),
    # An edge into the twin that duplicates an edge into the kept row would break
    # uix_relation_from_id_to_id once redirected; the kept row's edge stays.
    (
        "relation",
        ("from_id", "to_id", "relation_type"),
        "DELETE FROM relation WHERE to_id = :twin AND EXISTS ("
        "SELECT 1 FROM relation AS kept WHERE kept.from_id = relation.from_id "
        "AND kept.to_id = :keeper AND kept.relation_type = relation.relation_type)",
    ),
    ("relation", ("to_id",), "UPDATE relation SET to_id = :keeper WHERE to_id = :twin"),
    (
        "note_file_vacate",
        ("entity_id",),
        "UPDATE note_file_vacate SET entity_id = :keeper WHERE entity_id = :twin",
    ),
    (
        "accepted_project_note_change",
        ("entity_id",),
        "UPDATE accepted_project_note_change SET entity_id = :keeper WHERE entity_id = :twin",
    ),
    ("observation", ("entity_id",), "DELETE FROM observation WHERE entity_id = :twin"),
    ("note_section", ("entity_id",), "DELETE FROM note_section WHERE entity_id = :twin"),
    (
        "memory_time_index",
        ("entity_id",),
        "DELETE FROM memory_time_index WHERE entity_id = :twin",
    ),
    (
        "relation_search_refresh",
        ("entity_id",),
        "DELETE FROM relation_search_refresh WHERE entity_id = :twin",
    ),
    ("note_content", ("entity_id",), "DELETE FROM note_content WHERE entity_id = :twin"),
    ("search_index", ("entity_id",), "DELETE FROM search_index WHERE entity_id = :twin"),
    (
        "search_vector_chunks",
        ("entity_id",),
        "DELETE FROM search_vector_chunks WHERE entity_id = :twin",
    ),
)

# (table, key column, path columns) for every stored project-relative path.
PATH_COLUMNS = (
    ("entity", "id", ("file_path",)),
    ("note_content", "entity_id", ("file_path",)),
    ("note_file_vacate", "id", ("file_path",)),
    ("accepted_project_note_change", "id", ("file_path", "previous_file_path")),
    # search_index has no single-column key on every backend; the entity id plus
    # the stored path identifies the rows, so the old path is matched as well.
    ("search_index", "entity_id", ("file_path",)),
)


def _nfc(path: str) -> str:
    return unicodedata.normalize("NFC", path)


def _nfc_or_none(path: str | None) -> str | None:
    return None if path is None else _nfc(path)


def _table_columns(connection: sa.Connection) -> dict[str, set[str]]:
    inspector = sa.inspect(connection)
    return {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }


def _has(columns: dict[str, set[str]], table: str, *names: str) -> bool:
    return table in columns and set(names) <= columns[table]


def _keep_unwritten_note_content(
    connection: sa.Connection, columns: dict[str, set[str]], keeper: int, twins: list[int]
) -> None:
    """Move an accepted write that has not reached disk yet onto the kept entity.

    A `synced` row equals the file on disk, so dropping it loses nothing. Any other
    status is accepted content that exists only in the database. `updated_at` is
    no guide: reconciliation refreshes it on rows whose bytes did not change.
    """
    if not _has(columns, "note_content", "entity_id", "file_write_status"):
        return
    rows = connection.execute(
        sa.text(
            "SELECT entity_id, file_write_status FROM note_content WHERE entity_id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": [keeper, *twins]},
    ).all()
    unwritten = [entity_id for entity_id, status in rows if status != "synced"]
    if len(unwritten) > 1:
        raise RuntimeError(
            "Entities "
            + ", ".join(str(entity_id) for entity_id in sorted(unwritten))
            + " are NFC/NFD spellings of one file and each holds an accepted write that "
            "has not been written to disk. Let the previous Basic Memory version finish "
            "writing them (or resolve the note by hand), then upgrade again."
        )
    owners = {entity_id for entity_id, _ in rows}
    source = unwritten[0] if unwritten else (keeper if keeper in owners else None)
    if source is None and owners:
        source = min(owners)
    if source is None or source == keeper:
        return
    connection.execute(
        sa.text("DELETE FROM note_content WHERE entity_id = :keeper"), {"keeper": keeper}
    )
    connection.execute(
        sa.text("UPDATE note_content SET entity_id = :keeper WHERE entity_id = :owner"),
        {"keeper": keeper, "owner": source},
    )


def _merge_twins(connection: sa.Connection, columns: dict[str, set[str]]) -> None:
    rows = connection.execute(
        sa.text("SELECT id, project_id, file_path FROM entity ORDER BY id")
    ).all()
    groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for entity_id, project_id, file_path in rows:
        groups[(project_id, _nfc(file_path))].append(entity_id)

    for ids in groups.values():
        if len(ids) < 2:
            continue
        keeper, twins = ids[0], ids[1:]
        _keep_unwritten_note_content(connection, columns, keeper, twins)
        for twin in twins:
            params = {"keeper": keeper, "twin": twin}
            for table, needed, statement in TWIN_STATEMENTS:
                if _has(columns, table, *needed):
                    connection.execute(sa.text(statement), params)
            connection.execute(sa.text("DELETE FROM entity WHERE id = :twin"), params)
        connection.execute(
            sa.text("UPDATE entity SET checksum = NULL, sync_checksum = NULL WHERE id = :keeper"),
            {"keeper": keeper},
        )


def _file_checksum(project_root: str | None, file_path: str) -> str | None:
    """SHA-256 of the file on disk, the same digest the indexer stores as a guard."""
    if not project_root:
        return None
    try:
        return hashlib.sha256((Path(project_root) / file_path).read_bytes()).hexdigest()
    except OSError:
        return None


def _merge_vacate_markers(connection: sa.Connection, columns: dict[str, set[str]]) -> None:
    """Keep one marker per vacated path, since markers are unique by path.

    The marker whose checksum guard matches the file on disk is the current one;
    ids and timestamps are no guide because a refreshed marker keeps its id. When
    no guard matches (or the file is gone) every marker is already stale, and the
    lowest id is kept, which leaves the state the database held before.
    """
    if not _has(columns, "note_file_vacate", "id", "project_id", "file_path", "file_checksum"):
        return
    project_roots = {
        project_id: path
        for project_id, path in connection.execute(sa.text("SELECT id, path FROM project")).all()
    }
    groups: dict[tuple[int, str], list[tuple[int, str | None]]] = defaultdict(list)
    for marker_id, project_id, file_path, checksum in connection.execute(
        sa.text("SELECT id, project_id, file_path, file_checksum FROM note_file_vacate ORDER BY id")
    ).all():
        groups[(project_id, _nfc(file_path))].append((marker_id, checksum))
    for (project_id, file_path), markers in groups.items():
        if len(markers) < 2:
            continue
        on_disk = _file_checksum(project_roots.get(project_id), file_path)
        current = next((marker for marker in markers if marker[1] == on_disk), markers[0])
        for marker_id, _ in markers:
            if marker_id != current[0]:
                connection.execute(
                    sa.text("DELETE FROM note_file_vacate WHERE id = :id"), {"id": marker_id}
                )


def _normalize_paths(connection: sa.Connection, columns: dict[str, set[str]]) -> None:
    for table, key, path_columns in PATH_COLUMNS:
        if not _has(columns, table, key, *path_columns):
            continue
        selected = ", ".join(path_columns)
        rows = connection.execute(sa.text(f"SELECT DISTINCT {key}, {selected} FROM {table}")).all()
        for row in rows:
            row_key, values = row[0], tuple(row[1:])
            normalized = tuple(_nfc_or_none(value) for value in values)
            if normalized == values:
                continue
            assignments = ", ".join(f"{column} = :new_{column}" for column in path_columns)
            matches = " AND ".join(
                f"{column} IS NULL" if value is None else f"{column} = :old_{column}"
                for column, value in zip(path_columns, values)
            )
            connection.execute(
                sa.text(f"UPDATE {table} SET {assignments} WHERE {key} = :key AND {matches}"),
                {
                    "key": row_key,
                    **{f"new_{column}": value for column, value in zip(path_columns, normalized)},
                    **{f"old_{column}": value for column, value in zip(path_columns, values)},
                },
            )


def upgrade() -> None:
    """Merge NFC/NFD twin entities and store every path in NFC (macOS only)."""
    if sys.platform != "darwin":
        return
    connection = op.get_bind()
    columns = _table_columns(connection)
    _merge_twins(connection, columns)
    _merge_vacate_markers(connection, columns)
    _normalize_paths(connection, columns)


def downgrade() -> None:
    """Keep the NFC paths; the older schema reads them as ordinary paths."""
