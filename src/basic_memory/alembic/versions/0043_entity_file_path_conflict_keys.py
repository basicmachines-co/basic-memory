"""Store each entity's file path conflict keys so conflict checks are an indexed lookup.

Every note create and edit asks whether another file in the project names the same note:
a path equal after case, Unicode, and separator normalization, or a path that generates
the same permalink. That check loaded every file path in the project and normalized each
one in Python, so its cost grew with project size (about 130 ms of event-loop time per
write at 50,000 files).

``entity.file_path_key`` and ``entity.file_path_permalink`` now hold those two forms for
every row, and two indexes scoped by project find the matches directly. The backfill
computes the values in Python with the same function the application uses, in id-ordered
batches, so SQLite and Postgres hold identical values and large projects never load at once.

SQLite cannot add a NOT NULL column without a default, and rebuilding ``entity`` in batch
mode duplicates its generated columns (see x7d8e9f0a1b2). On SQLite the columns are added
NOT NULL with an empty-string default that only exists to satisfy ALTER TABLE; every row is
backfilled here and every writer sets both columns. Postgres adds them nullable, backfills,
then sets NOT NULL with no default.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-01 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# The stored keys must be byte-identical to what the running application computes for
# new writes, so the backfill uses the application's own function rather than a copy.
from basic_memory.utils import file_path_conflict_keys

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKFILL_BATCH_SIZE = 1000
CONFLICT_KEY_COLUMNS = ("file_path_key", "file_path_permalink")
CONFLICT_KEY_INDEXES = (
    ("ix_entity_project_file_path_key", "file_path_key"),
    ("ix_entity_project_file_path_permalink", "file_path_permalink"),
)


def _backfill(connection: sa.Connection) -> None:
    """Compute both keys for every entity row, one id-ordered batch at a time."""
    last_id = 0
    while True:
        rows = connection.execute(
            sa.text(
                "SELECT id, file_path FROM entity WHERE id > :last_id ORDER BY id LIMIT :limit"
            ),
            {"last_id": last_id, "limit": BACKFILL_BATCH_SIZE},
        ).all()
        if not rows:
            return
        updates = []
        for entity_id, file_path in rows:
            keys = file_path_conflict_keys(file_path)
            updates.append(
                {"id": entity_id, "path_key": keys.path_key, "permalink": keys.permalink}
            )
        connection.execute(
            sa.text(
                "UPDATE entity SET file_path_key = :path_key, file_path_permalink = :permalink "
                "WHERE id = :id"
            ),
            updates,
        )
        last_id = rows[-1][0]


def upgrade() -> None:
    """Add, backfill, and index the file path conflict keys."""
    connection = op.get_bind()
    is_postgres = connection.dialect.name == "postgresql"

    for column_name in CONFLICT_KEY_COLUMNS:
        if is_postgres:
            op.execute(f"ALTER TABLE entity ADD COLUMN {column_name} VARCHAR")
        else:
            op.execute(f"ALTER TABLE entity ADD COLUMN {column_name} VARCHAR NOT NULL DEFAULT ''")

    _backfill(connection)

    if is_postgres:
        for column_name in CONFLICT_KEY_COLUMNS:
            op.execute(f"ALTER TABLE entity ALTER COLUMN {column_name} SET NOT NULL")

    for index_name, column_name in CONFLICT_KEY_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON entity (project_id, {column_name})")


def downgrade() -> None:
    """Drop the conflict keys and their indexes."""
    for index_name, _ in CONFLICT_KEY_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    for column_name in CONFLICT_KEY_COLUMNS:
        op.drop_column("entity", column_name)
