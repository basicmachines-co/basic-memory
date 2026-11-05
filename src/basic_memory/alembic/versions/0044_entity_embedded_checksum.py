"""Record which checksum each entity's embeddings were built from.

Search responses now say how current semantic results are: the oldest last-modified
time among notes whose embeddings lag their content. Basic Memory Cloud defers a
workspace's embeddings past a daily fair-use budget while notes stay saved and
full-text indexed, so a note can sit with new content and old vectors for a day.
Nothing in the schema showed that: an edited note keeps its earlier `ready` chunks, which
look the same as current ones.

``entity.embedded_checksum`` holds the checksum a vector-sync pass finished embedding.
A note is behind when it is NULL, differs from ``checksum``, or the last pass left the
note part-way (``vector_sync_deferred_at``). A partial index on exactly those rows makes
the per-search lookup read only the notes that are behind.

The backfill treats every entity the last pass did not leave part-way as current. That
is not provable from the manifest, but the alternative, NULL everywhere, would report
notes that owe nothing (opted out of embeddings, or with no text to embed) as behind
until something rewrites them. A note that was in fact behind at upgrade time reads as
current until its pending embedding pass runs and stamps the real value.

A plain ALTER TABLE, not batch mode: ``entity`` carries generated columns, and SQLite's
batch recreation emits them twice (see x7d8e9f0a1b2).

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-04 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE in models/knowledge.py. Copied, not
# imported, so this revision keeps creating the index it was written with.
EMBEDDING_NOT_CURRENT_PREDICATE = (
    "content_type = 'text/markdown' AND ("
    "embedded_checksum IS NULL "
    "OR embedded_checksum <> checksum "
    "OR vector_sync_deferred_at IS NOT NULL)"
)


def upgrade() -> None:
    """Add entity.embedded_checksum, backfill it, and index the entities that are behind."""
    op.add_column("entity", sa.Column("embedded_checksum", sa.String(), nullable=True))
    op.execute(
        "UPDATE entity SET embedded_checksum = checksum WHERE vector_sync_deferred_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_entity_project_embedding_not_current "
        "ON entity (project_id, updated_at) "
        f"WHERE {EMBEDDING_NOT_CURRENT_PREDICATE}"
    )


def downgrade() -> None:
    """Drop the index and the embedded checksum."""
    op.execute("DROP INDEX IF EXISTS ix_entity_project_embedding_not_current")
    op.drop_column("entity", "embedded_checksum")
