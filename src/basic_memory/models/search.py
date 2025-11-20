"""Search models and tables."""

from sqlalchemy import DDL, Column, Integer, String, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from basic_memory.models.base import Base


class SearchIndex(Base):
    """Search index table for Postgres only.

    For SQLite: This model is skipped; FTS5 virtual table is created via DDL instead.
    For Postgres: This is the actual table structure with tsvector support.
    """

    __tablename__ = "search_index"

    # Primary key (rowid in SQLite FTS5, explicit id in Postgres)
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core searchable fields
    title = Column(Text, nullable=True)
    content_stems = Column(Text, nullable=True)
    content_snippet = Column(Text, nullable=True)
    permalink = Column(String(255), nullable=True, index=True)
    file_path = Column(Text, nullable=True)
    type = Column(String(50), nullable=True)

    # Project context
    project_id = Column(Integer, nullable=True, index=True)

    # Relation fields
    from_id = Column(Integer, nullable=True)
    to_id = Column(Integer, nullable=True)
    relation_type = Column(String(100), nullable=True)

    # Observation fields
    entity_id = Column(Integer, nullable=True)
    category = Column(String(100), nullable=True)

    # Common fields
    # Use JSONB for Postgres, JSON for SQLite
    # Note: 'metadata' is a reserved name in SQLAlchemy, so we use 'metadata_' and map to 'metadata'
    metadata_ = Column("metadata", JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    # Note: textsearchable_index_col (tsvector) will be added by migration for Postgres only


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
