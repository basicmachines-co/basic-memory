"""Update search index schema

Revision ID: cc7172b46608
Revises: 502b60eaa905
Create Date: 2025-02-28 18:48:23.244941

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "cc7172b46608"
down_revision: Union[str, None] = "502b60eaa905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema to use new search index with content_stems and content_snippet."""

    # This migration is SQLite-specific (FTS5 virtual tables)
    # For Postgres, the search_index table is created via ORM models
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return

    # First, drop the existing search_index table
    op.execute("DROP TABLE IF EXISTS search_index")

    # Create new search_index with updated schema
    op.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
        -- Core entity fields
        id UNINDEXED,          -- Row ID
        title,                 -- Title for searching
        content_stems,         -- Main searchable content split into stems
        content_snippet,       -- File content snippet for display
        permalink,             -- Stable identifier (now indexed for path search)
        file_path UNINDEXED,   -- Physical location
        type UNINDEXED,        -- entity/relation/observation
        
        -- Relation fields 
        from_id UNINDEXED,     -- Source entity
        to_id UNINDEXED,       -- Target entity
        relation_type UNINDEXED, -- Type of relation
        
        -- Observation fields
        entity_id UNINDEXED,   -- Parent entity
        category UNINDEXED,    -- Observation category
        
        -- Common fields
        metadata UNINDEXED,    -- JSON metadata
        created_at UNINDEXED,  -- Creation timestamp
        updated_at UNINDEXED,  -- Last update
        
        -- Configuration
        tokenize='unicode61 tokenchars 0x2F',  -- Hex code for /
        prefix='1,2,3,4'                    -- Support longer prefixes for paths
    );
    """)


def downgrade() -> None:
    """Downgrade database schema to use old search index."""

    # This migration is SQLite-specific (FTS5 virtual tables)
    # For Postgres, the search_index table is managed via ORM models
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return

    # Drop the updated search_index table
    op.execute("DROP TABLE IF EXISTS search_index")

    # Recreate the original search_index schema
    op.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
        -- Core entity fields
        id UNINDEXED,          -- Row ID
        title,                 -- Title for searching
        content,               -- Main searchable content
        permalink,             -- Stable identifier (now indexed for path search)
        file_path UNINDEXED,   -- Physical location
        type UNINDEXED,        -- entity/relation/observation
        
        -- Relation fields 
        from_id UNINDEXED,     -- Source entity
        to_id UNINDEXED,       -- Target entity
        relation_type UNINDEXED, -- Type of relation
        
        -- Observation fields
        entity_id UNINDEXED,   -- Parent entity
        category UNINDEXED,    -- Observation category
        
        -- Common fields
        metadata UNINDEXED,    -- JSON metadata
        created_at UNINDEXED,  -- Creation timestamp
        updated_at UNINDEXED,  -- Last update
        
        -- Configuration
        tokenize='unicode61 tokenchars 0x2F',  -- Hex code for /
        prefix='1,2,3,4'                    -- Support longer prefixes for paths
    );
    """)

    # Print instruction to manually reindex after migration
    print("\n------------------------------------------------------------------")
    print("IMPORTANT: After downgrade completes, manually run the reindex command:")
    print("basic-memory sync")
    print("------------------------------------------------------------------\n")
