"""Search DDL statements for SQLite and Postgres.

The search_index table is created via raw DDL, not ORM models, because:
- SQLite uses FTS5 virtual tables (cannot be represented as ORM)
- Postgres uses composite primary keys and generated tsvector columns
- Both backends use raw SQL for all search operations via SearchIndexRow dataclass
"""

from sqlalchemy import DDL


# Define Postgres search_index table with composite primary key and tsvector
# This DDL matches the Alembic migration schema (314f1ea54dc4)
# Used by tests to create the table without running full migrations
# NOTE: Split into separate DDL statements because asyncpg doesn't support
# multiple statements in a single execute call.
CREATE_POSTGRES_SEARCH_INDEX_TABLE = DDL("""
CREATE TABLE IF NOT EXISTS search_index (
    id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    title TEXT,
    content_stems TEXT,
    content_snippet TEXT,
    permalink VARCHAR,
    file_path VARCHAR,
    type VARCHAR,
    from_id INTEGER,
    to_id INTEGER,
    relation_type VARCHAR,
    entity_id INTEGER,
    category VARCHAR,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE,
    textsearchable_index_col tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content_stems, ''))
    ) STORED,
    PRIMARY KEY (id, type, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
)
""")

CREATE_POSTGRES_SEARCH_INDEX_FTS = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_fts ON search_index USING gin(textsearchable_index_col)
""")

CREATE_POSTGRES_SEARCH_INDEX_METADATA = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_metadata_gin ON search_index USING gin(metadata jsonb_path_ops)
""")

# Define FTS5 virtual table creation for SQLite only
# This DDL is executed separately for SQLite databases
CREATE_SEARCH_INDEX = DDL("""
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    -- Core entity fields
    id UNINDEXED,          -- Row ID
    title,                 -- Title for searching
    content_stems,         -- Main searchable content split into stems
    content_snippet,       -- File content snippet for display
    permalink,             -- Stable identifier (now indexed for path search)
    file_path UNINDEXED,   -- Physical location
    type UNINDEXED,        -- entity/relation/observation

    -- Project context
    project_id UNINDEXED,  -- Project identifier

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
