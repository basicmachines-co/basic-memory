"""Migration coverage for the portable script n-gram FTS channel."""

import sqlite3
from importlib import import_module
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine


migration = import_module(
    "basic_memory.alembic.versions.d2e3f4a5b6c7_add_script_ngrams_to_full_text_search"
)


def test_sqlite_upgrade_and_downgrade_preserve_word_search_rows(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "script-ngrams.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE VIRTUAL TABLE search_index USING fts5(
                id UNINDEXED, title, content_stems, content_snippet, permalink,
                file_path UNINDEXED, type UNINDEXED, project_id UNINDEXED,
                from_id UNINDEXED, to_id UNINDEXED, relation_type UNINDEXED,
                entity_id UNINDEXED, category UNINDEXED, metadata UNINDEXED,
                created_at UNINDEXED, updated_at UNINDEXED,
                tokenize='unicode61 tokenchars 0x2F', prefix='1,2,3,4'
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO search_index (id, title, content_stems, project_id)
            VALUES (1, 'Existing title', 'existing words', 7)
        """)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(search_index)")]
        assert "script_ngrams" in columns
        assert connection.execute(
            "SELECT id, title, script_ngrams FROM search_index"
        ).fetchall() == [(1, "Existing title", "")]

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.downgrade()

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(search_index)")]
        assert "script_ngrams" not in columns
        assert connection.execute("SELECT id, title FROM search_index").fetchall() == [
            (1, "Existing title")
        ]


def test_sqlite_upgrade_creates_search_index_for_a_fresh_database(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "fresh-script-ngrams.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(search_index)")]
        assert "script_ngrams" in columns


def test_postgres_upgrade_and_downgrade_manage_both_script_indexes(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "get_bind",
        lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.upgrade()
    migration.downgrade()

    sql = "\n".join(statements)
    assert "idx_search_index_script_ngrams_fts" in sql
    assert "idx_search_index_fts_chunks_script_ngrams_fts" in sql
    assert "to_tsvector('simple', script_ngrams)" in sql
    assert "DROP COLUMN IF EXISTS script_ngrams" in sql
