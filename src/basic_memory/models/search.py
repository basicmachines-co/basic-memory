"""Search DDL statements for SQLite and Postgres.

The search_index table is created via raw DDL, not ORM models, because:
- SQLite uses FTS5 virtual tables (cannot be represented as ORM)
- Postgres uses composite primary keys and generated tsvector columns
- Both backends use raw SQL for all search operations via SearchIndexRow dataclass
"""

from typing import Final

from sqlalchemy import DDL


# --- Search index row identity ---

# What makes one search_index row distinct from another, stated once because the two
# backends, three write paths, and the Alembic migration all have to agree on it.
#
# A permalink alone is not an identity. A relation's permalink is `from/type/to` with the
# relation type authored by the user, so a note that says
#
#     - [decision] redis
#     - observations [[decision/redis]]
#
# hands its observation and its relation the same string, and no reserved path segment
# closes that: the colliding segment is the author's own text (#1437). The row kind is
# already half of this table's primary key, `(id, type, project_id)`; uniqueness keyed on
# the permalink alone was the outlier, narrower than the table's own notion of identity.
SEARCH_INDEX_ROW_KEY: Final = ("permalink", "type", "project_id")

# The same key rendered for the two SQL shapes that need it: an index/conflict-target
# column list, and an equality predicate over bound parameters of the same names.
SEARCH_INDEX_ROW_KEY_COLUMNS: Final = ", ".join(SEARCH_INDEX_ROW_KEY)
SEARCH_INDEX_ROW_KEY_PREDICATE: Final = " AND ".join(
    f"{column} = :{column}" for column in SEARCH_INDEX_ROW_KEY
)


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
    script_ngrams TEXT NOT NULL DEFAULT '',
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
        to_tsvector(
            'english',
            coalesce(title, '') || ' ' ||
            coalesce(content_stems, '')
        )
    ) STORED,
    script_ngrams_index_col tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', script_ngrams)
    ) STORED,
    PRIMARY KEY (id, type, project_id),
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
)
""")

CREATE_POSTGRES_SEARCH_INDEX_FTS = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_fts ON search_index USING gin(textsearchable_index_col)
""")

CREATE_POSTGRES_SEARCH_INDEX_SCRIPT_NGRAMS_FTS = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_script_ngrams_fts
ON search_index USING gin(script_ngrams_index_col)
""")

# Full note bodies are stored in bounded child rows so one unusually large note
# cannot exceed PostgreSQL's per-tsvector size limit.
CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_TABLE = DDL("""
CREATE TABLE IF NOT EXISTS search_index_fts_chunks (
    project_id INTEGER NOT NULL,
    search_index_id INTEGER NOT NULL,
    search_index_type VARCHAR NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    script_ngrams TEXT NOT NULL DEFAULT '',
    textsearchable_index_col tsvector GENERATED ALWAYS AS (
        to_tsvector('english', chunk_text)
    ) STORED,
    script_ngrams_index_col tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', script_ngrams)
    ) STORED,
    PRIMARY KEY (project_id, search_index_id, search_index_type, chunk_index),
    FOREIGN KEY (search_index_id, search_index_type, project_id)
        REFERENCES search_index(id, type, project_id)
        ON UPDATE CASCADE ON DELETE CASCADE
)
""")

CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_INDEX = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_fts_chunks_fts
ON search_index_fts_chunks USING gin(textsearchable_index_col)
""")

CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_SCRIPT_NGRAMS_INDEX = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_fts_chunks_script_ngrams_fts
ON search_index_fts_chunks USING gin(script_ngrams_index_col)
""")

CREATE_POSTGRES_SEARCH_INDEX_METADATA = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_index_metadata_gin ON search_index USING gin(metadata jsonb_path_ops)
""")

# Partial unique index on the row key for non-null permalinks. This prevents a second
# row of the same kind from claiming an address that kind already owns, and is the
# conflict target the Postgres upserts use to resolve races during parallel indexing.
# See SEARCH_INDEX_ROW_KEY for why the row kind belongs in the key.
CREATE_POSTGRES_SEARCH_INDEX_PERMALINK = DDL(f"""
CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_type_project
ON search_index ({SEARCH_INDEX_ROW_KEY_COLUMNS})
WHERE permalink IS NOT NULL
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
    script_ngrams,         -- Portable bigrams for scripts without word boundaries
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

# Postgres semantic chunk metadata table.
# Matches the Alembic migration (h1b2c3d4e5f6) schema.
# Used by tests to create the table without running full migrations.
CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_TABLE = DDL("""
CREATE TABLE IF NOT EXISTS search_vector_chunks (
    id BIGSERIAL PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_index TEXT NOT NULL,
    embedding_status TEXT NOT NULL CHECK (embedding_status IN ('pending', 'ready')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, entity_id, chunk_key)
)
""")

CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_INDEX = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_project_entity
ON search_vector_chunks (project_id, entity_id)
""")

# Local semantic chunk metadata table for SQLite.
# Embedding vectors live in sqlite-vec virtual table keyed by this table rowid.
CREATE_SQLITE_SEARCH_VECTOR_CHUNKS = DDL("""
CREATE TABLE IF NOT EXISTS search_vector_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_index TEXT NOT NULL,
    embedding_status TEXT NOT NULL CHECK (embedding_status IN ('pending', 'ready')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
""")

CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY = DDL("""
CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_project_entity
ON search_vector_chunks (project_id, entity_id)
""")

CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE = DDL("""
CREATE UNIQUE INDEX IF NOT EXISTS uix_search_vector_chunks_entity_key
ON search_vector_chunks (project_id, entity_id, chunk_key)
""")


def create_sqlite_search_vector_embeddings(dimensions: int) -> DDL:
    """Build sqlite-vec virtual table DDL for the configured embedding dimension."""
    return DDL(
        f"""
CREATE VIRTUAL TABLE IF NOT EXISTS search_vector_embeddings
USING vec0(
    embedding float[{dimensions}],
    +source_hash text
)
"""
    )
