"""Search models and tables."""

from sqlalchemy import DDL

# Define FTS5 virtual table creation
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
