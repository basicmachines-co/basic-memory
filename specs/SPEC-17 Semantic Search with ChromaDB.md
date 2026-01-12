---
title: 'SPEC-17: Semantic Search with ChromaDB'
type: spec
permalink: specs/spec-17-semantic-search-chromadb
tags:
- search
- chromadb
- semantic-search
- vector-database
- postgres-migration
---

# SPEC-17: Semantic Search with ChromaDB

Why ChromaDB for Knowledge Management

Your users aren't just searching for keywords - they're trying to:
- "Find notes related to this concept"
- "Show me similar ideas"
- "What else did I write about this topic?"

Example:
    # User searches: "AI ethics"

    # FTS5/MeiliSearch finds:
    - "AI ethics guidelines"     ✅
    - "ethical AI development"   ✅
    - "artificial intelligence"  ❌ No keyword match

    # ChromaDB finds:
    - "AI ethics guidelines"     ✅
    - "ethical AI development"   ✅
    - "artificial intelligence"  ✅ Semantic match!
    - "bias in ML models"        ✅ Related concept
    - "responsible technology"   ✅ Similar theme
    - "neural network fairness"  ✅ Connected idea

ChromaDB vs MeiliSearch vs Typesense

| Feature          | ChromaDB           | MeiliSearch        | Typesense          |
|------------------|--------------------|--------------------|--------------------|
| Semantic Search  | ✅ Excellent        | ❌ No               | ❌ No               |
| Keyword Search   | ⚠️ Via metadata    | ✅ Excellent        | ✅ Excellent        |
| Local Deployment | ✅ Embedded mode    | ⚠️ Server required | ⚠️ Server required |
| No Server Needed | ✅ YES!             | ❌ No               | ❌ No               |
| Embedding Cost   | ~$0.13/1M tokens   | None               | None               |
| Search Speed     | 50-200ms           | 10-50ms            | 10-50ms            |
| Best For         | Semantic discovery | Exact terms        | Exact terms        |

The Killer Feature: Embedded Mode

ChromaDB has an embedded client that runs in-process - NO SERVER NEEDED!

# Local (FOSS) - ChromaDB embedded in Python process
import chromadb

client = chromadb.PersistentClient(path="/path/to/chroma_data")
collection = client.get_or_create_collection("knowledge_base")

# Add documents
collection.add(
  ids=["note1", "note2"],
  documents=["AI ethics", "Neural networks"],
  metadatas=[{"type": "note"}, {"type": "spec"}]
)

# Search - NO API calls, runs locally!
results = collection.query(
  query_texts=["machine learning"],
  n_results=10
)


## Why

### Current Problem: Database Persistence in Cloud
In cloud deployments, `memory.db` (SQLite) doesn't persist across Docker container restarts. This means:
- Database must be rebuilt on every container restart
- Initial sync takes ~49 seconds for 500 files (after optimization in #352)
- Users experience delays on each deployment

### Search Architecture Issues
Current SQLite FTS5 implementation creates a **dual-implementation problem** for PostgreSQL migration:
- FTS5 (SQLite) uses `VIRTUAL TABLE` with `MATCH` queries
- PostgreSQL full-text search uses `TSVECTOR` with `@@` operator
- These are fundamentally incompatible architectures
- Would require **2x search code** and **2x tests** to support both

**Example of incompatibility:**
```python
# SQLite FTS5
"content_stems MATCH :text"

# PostgreSQL
"content_vector @@ plainto_tsquery(:text)"
```

### Search Quality Limitations
Current keyword-based FTS5 has limitations:
- No semantic understanding (search "AI" doesn't find "machine learning")
- No word relationships (search "neural networks" doesn't find "deep learning")
- Limited typo tolerance
- No relevance ranking beyond keyword matching

### Strategic Goal: PostgreSQL Migration
Moving to PostgreSQL (Neon) for cloud deployments would:
- ✅ Solve persistence issues (database survives restarts)
- ✅ Enable multi-tenant architecture
- ✅ Better performance for large datasets
- ✅ Support for cloud-native scaling

**But requires solving the search compatibility problem.**

## What

Migrate from SQLite FTS5 to **ChromaDB** for semantic vector search across all deployments.

**Key insight:** ChromaDB is **database-agnostic** - it works with both SQLite and PostgreSQL, eliminating the dual-implementation problem.

### Affected Areas
- Search implementation (`src/basic_memory/repository/search_repository.py`)
- Search service (`src/basic_memory/services/search_service.py`)
- Search models (`src/basic_memory/models/search.py`)
- Database initialization (`src/basic_memory/db.py`)
- MCP search tools (`src/basic_memory/mcp/tools/search.py`)
- Dependencies (`pyproject.toml` - add ChromaDB)
- Alembic migrations (FTS5 table removal)
- Documentation

### What Changes
**Removed:**
- SQLite FTS5 virtual table
- `MATCH` query syntax
- FTS5-specific tokenization and prefix handling
- ~300 lines of FTS5 query preparation code

**Added:**
- ChromaDB persistent client (embedded mode)
- Vector embedding generation
- Semantic similarity search
- Local embedding model (`sentence-transformers`)
- Collection management for multi-project support

### What Stays the Same
- Search API interface (MCP tools, REST endpoints)
- Entity/Observation/Relation indexing workflow
- Multi-project isolation
- Search filtering by type, date, metadata
- Pagination and result formatting
- **All SQL queries for exact lookups and metadata filtering**

## Hybrid Architecture: SQL + ChromaDB

**Critical Design Decision:** ChromaDB **complements** SQL, it doesn't **replace** it.

### Why Hybrid?

ChromaDB is excellent for semantic text search but terrible for exact lookups. SQL is perfect for exact lookups and structured queries. We use both:

```
┌─────────────────────────────────────────────────┐
│ Search Request                                   │
└─────────────────────────────────────────────────┘
                    ▼
       ┌────────────────────────┐
       │ SearchRepository       │
       │  (Smart Router)        │
       └────────────────────────┘
              ▼           ▼
  ┌───────────┐      ┌──────────────┐
  │ SQL       │      │ ChromaDB     │
  │ Queries   │      │ Semantic     │
  └───────────┘      └──────────────┘
       ▼                    ▼
  Exact lookups      Text search
  - Permalink        - Semantic similarity
  - Pattern match    - Related concepts
  - Title exact      - Typo tolerance
  - Metadata filter  - Fuzzy matching
  - Date ranges
```

### When to Use Each

#### Use SQL For (Fast & Exact)

**Exact Permalink Lookup:**
```python
# Find by exact permalink - SQL wins
"SELECT * FROM entities WHERE permalink = 'specs/search-feature'"
# ~1ms, perfect for exact matches

# ChromaDB would be: ~50ms, wasteful
```

**Pattern Matching:**
```python
# Find all specs - SQL wins
"SELECT * FROM entities WHERE permalink GLOB 'specs/*'"
# ~5ms, perfect for wildcards

# ChromaDB doesn't support glob patterns
```

**Pure Metadata Queries:**
```python
# Find all meetings tagged "important" - SQL wins
"SELECT * FROM entities
 WHERE json_extract(entity_metadata, '$.entity_type') = 'meeting'
 AND json_extract(entity_metadata, '$.tags') LIKE '%important%'"
# ~5ms, structured query

# No text search needed, SQL is faster and simpler
```

**Date Filtering:**
```python
# Find recent specs - SQL wins
"SELECT * FROM entities
 WHERE entity_type = 'spec'
 AND created_at > '2024-01-01'
 ORDER BY created_at DESC"
# ~2ms, perfect for structured data
```

#### Use ChromaDB For (Semantic & Fuzzy)

**Semantic Content Search:**
```python
# Find notes about "neural networks" - ChromaDB wins
collection.query(query_texts=["neural networks"])
# Finds: "machine learning", "deep learning", "AI models"
# ~50-100ms, semantic understanding

# SQL FTS5 would only find exact keyword matches
```

**Text Search + Metadata:**
```python
# Find meeting notes about "project planning" tagged "important"
collection.query(
    query_texts=["project planning"],
    where={
        "entity_type": "meeting",
        "tags": {"$contains": "important"}
    }
)
# ~100ms, semantic search with filters
# Finds: "roadmap discussion", "sprint planning", etc.
```

**Typo Tolerance:**
```python
# User types "serch feature" (typo) - ChromaDB wins
collection.query(query_texts=["serch feature"])
# Still finds: "search feature" documents
# ~50-100ms, fuzzy matching

# SQL would find nothing
```

### Performance Comparison

| Query Type | SQL | ChromaDB | Winner |
|-----------|-----|----------|--------|
| Exact permalink | 1-2ms | 50ms | ✅ SQL |
| Pattern match (specs/*) | 5-10ms | N/A | ✅ SQL |
| Pure metadata filter | 5ms | 50ms | ✅ SQL |
| Semantic text search | ❌ Can't | 50-100ms | ✅ ChromaDB |
| Text + metadata | ❌ Keywords only | 100ms | ✅ ChromaDB |
| Typo tolerance | ❌ Can't | 50ms | ✅ ChromaDB |

### Metadata/Frontmatter Handling

**Both systems support full frontmatter filtering!**

#### SQL Metadata Storage

```python
# Entities table stores frontmatter as JSON
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    title TEXT,
    permalink TEXT,
    file_path TEXT,
    entity_type TEXT,
    entity_metadata JSON,  -- All frontmatter here!
    created_at DATETIME,
    ...
)

# Query frontmatter fields
SELECT * FROM entities
WHERE json_extract(entity_metadata, '$.entity_type') = 'meeting'
  AND json_extract(entity_metadata, '$.tags') LIKE '%important%'
  AND json_extract(entity_metadata, '$.status') = 'completed'
```

#### ChromaDB Metadata Storage

```python
# When indexing, store ALL frontmatter as metadata
class ChromaSearchBackend:
    async def index_entity(self, entity: Entity):
        """Index with complete frontmatter metadata."""

        # Extract ALL frontmatter fields
        metadata = {
            "entity_id": entity.id,
            "project_id": entity.project_id,
            "permalink": entity.permalink,
            "file_path": entity.file_path,
            "entity_type": entity.entity_type,
            "type": "entity",
            # ALL frontmatter tags
            "tags": entity.entity_metadata.get("tags", []),
            # Custom frontmatter fields
            "status": entity.entity_metadata.get("status"),
            "priority": entity.entity_metadata.get("priority"),
            # Spread any other custom fields
            **{k: v for k, v in entity.entity_metadata.items()
               if k not in ["tags", "entity_type"]}
        }

        self.collection.upsert(
            ids=[f"entity_{entity.id}_{entity.project_id}"],
            documents=[self._format_document(entity)],
            metadatas=[metadata]  # Full frontmatter!
        )
```

#### ChromaDB Metadata Queries

ChromaDB supports rich filtering:

```python
# Simple filter - single field
collection.query(
    query_texts=["project planning"],
    where={"entity_type": "meeting"}
)

# Multiple conditions (AND)
collection.query(
    query_texts=["architecture decisions"],
    where={
        "entity_type": "spec",
        "tags": {"$contains": "important"}
    }
)

# Complex filters with operators
collection.query(
    query_texts=["machine learning"],
    where={
        "$and": [
            {"entity_type": {"$in": ["note", "spec"]}},
            {"tags": {"$contains": "AI"}},
            {"created_at": {"$gt": "2024-01-01"}},
            {"status": "in-progress"}
        ]
    }
)

# Multiple tags (all must match)
collection.query(
    query_texts=["cloud architecture"],
    where={
        "$and": [
            {"tags": {"$contains": "architecture"}},
            {"tags": {"$contains": "cloud"}}
        ]
    }
)
```

### Smart Routing Implementation

```python
class SearchRepository:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        project_id: int,
        chroma_backend: ChromaSearchBackend
    ):
        self.sql = session_maker  # Keep SQL!
        self.chroma = chroma_backend
        self.project_id = project_id

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        types: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        custom_metadata: Optional[dict] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Smart routing between SQL and ChromaDB."""

        # ==========================================
        # Route 1: Exact Lookups → SQL (1-5ms)
        # ==========================================

        if permalink:
            # Exact permalink: "specs/search-feature"
            return await self._sql_permalink_lookup(permalink)

        if permalink_match:
            # Pattern match: "specs/*"
            return await self._sql_pattern_match(permalink_match)

        if title and not search_text:
            # Exact title lookup (no semantic search needed)
            return await self._sql_title_match(title)

        # ==========================================
        # Route 2: Pure Metadata → SQL (5-10ms)
        # ==========================================

        # No text search, just filtering by metadata
        if not search_text and (types or tags or after_date or custom_metadata):
            return await self._sql_metadata_filter(
                types=types,
                tags=tags,
                after_date=after_date,
                custom_metadata=custom_metadata,
                limit=limit,
                offset=offset
            )

        # ==========================================
        # Route 3: Text Search → ChromaDB (50-100ms)
        # ==========================================

        if search_text:
            # Build ChromaDB metadata filters
            where_filters = self._build_chroma_filters(
                types=types,
                tags=tags,
                after_date=after_date,
                custom_metadata=custom_metadata
            )

            # Semantic search with metadata filtering
            return await self.chroma.search(
                query_text=search_text,
                project_id=self.project_id,
                where=where_filters,
                limit=limit
            )

        # ==========================================
        # Route 4: List All → SQL (2-5ms)
        # ==========================================

        return await self._sql_list_entities(
            limit=limit,
            offset=offset
        )

    def _build_chroma_filters(
        self,
        types: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        custom_metadata: Optional[dict] = None
    ) -> dict:
        """Build ChromaDB where clause from filters."""
        filters = {"project_id": self.project_id}

        # Type filtering
        if types:
            if len(types) == 1:
                filters["entity_type"] = types[0]
            else:
                filters["entity_type"] = {"$in": types}

        # Tag filtering (array contains)
        if tags:
            if len(tags) == 1:
                filters["tags"] = {"$contains": tags[0]}
            else:
                # Multiple tags - all must match
                filters = {
                    "$and": [
                        filters,
                        *[{"tags": {"$contains": tag}} for tag in tags]
                    ]
                }

        # Date filtering
        if after_date:
            filters["created_at"] = {"$gt": after_date.isoformat()}

        # Custom frontmatter fields
        if custom_metadata:
            filters.update(custom_metadata)

        return filters

    async def _sql_metadata_filter(
        self,
        types: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        custom_metadata: Optional[dict] = None,
        limit: int = 10,
        offset: int = 0
    ) -> List[SearchIndexRow]:
        """Pure metadata queries using SQL."""
        conditions = ["project_id = :project_id"]
        params = {"project_id": self.project_id}

        if types:
            type_list = ", ".join(f"'{t}'" for t in types)
            conditions.append(f"entity_type IN ({type_list})")

        if tags:
            # Check each tag
            for i, tag in enumerate(tags):
                param_name = f"tag_{i}"
                conditions.append(
                    f"json_extract(entity_metadata, '$.tags') LIKE :{param_name}"
                )
                params[param_name] = f"%{tag}%"

        if after_date:
            conditions.append("created_at > :after_date")
            params["after_date"] = after_date

        if custom_metadata:
            for key, value in custom_metadata.items():
                param_name = f"meta_{key}"
                conditions.append(
                    f"json_extract(entity_metadata, '$.{key}') = :{param_name}"
                )
                params[param_name] = value

        where = " AND ".join(conditions)
        sql = f"""
            SELECT * FROM entities
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = limit
        params["offset"] = offset

        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(text(sql), params)
            return self._format_sql_results(result)
```

### Real-World Examples

#### Example 1: Pure Metadata Query (No Text)
```python
# "Find all meetings tagged 'important'"
results = await search_repo.search(
    types=["meeting"],
    tags=["important"]
)

# Routing: → SQL (~5ms)
# SQL: SELECT * FROM entities
#      WHERE entity_type = 'meeting'
#      AND json_extract(entity_metadata, '$.tags') LIKE '%important%'
```

#### Example 2: Semantic Search (No Metadata)
```python
# "Find notes about neural networks"
results = await search_repo.search(
    search_text="neural networks"
)

# Routing: → ChromaDB (~80ms)
# Finds: "machine learning", "deep learning", "AI models", etc.
```

#### Example 3: Semantic + Metadata
```python
# "Find meeting notes about 'project planning' tagged 'important'"
results = await search_repo.search(
    search_text="project planning",
    types=["meeting"],
    tags=["important"]
)

# Routing: → ChromaDB with filters (~100ms)
# ChromaDB: query_texts=["project planning"]
#           where={"entity_type": "meeting",
#                  "tags": {"$contains": "important"}}
# Finds: "roadmap discussion", "sprint planning", etc.
```

#### Example 4: Complex Frontmatter Query
```python
# "Find in-progress specs with multiple tags, recent"
results = await search_repo.search(
    types=["spec"],
    tags=["architecture", "cloud"],
    after_date=datetime(2024, 1, 1),
    custom_metadata={"status": "in-progress"}
)

# Routing: → SQL (~10ms)
# No text search, pure structured query - SQL is faster
```

#### Example 5: Semantic + Complex Metadata
```python
# "Find notes about 'authentication' that are in-progress"
results = await search_repo.search(
    search_text="authentication",
    custom_metadata={"status": "in-progress", "priority": "high"}
)

# Routing: → ChromaDB with metadata filters (~100ms)
# Semantic search for "authentication" concept
# Filters by status and priority in metadata
```

#### Example 6: Exact Permalink
```python
# "Show me specs/search-feature"
results = await search_repo.search(
    permalink="specs/search-feature"
)

# Routing: → SQL (~1ms)
# SQL: SELECT * FROM entities WHERE permalink = 'specs/search-feature'
```

#### Example 7: Pattern Match
```python
# "Show me all specs"
results = await search_repo.search(
    permalink_match="specs/*"
)

# Routing: → SQL (~5ms)
# SQL: SELECT * FROM entities WHERE permalink GLOB 'specs/*'
```

### What We Remove vs Keep

**REMOVE (FTS5-specific):**
- ❌ `CREATE VIRTUAL TABLE search_index USING fts5(...)`
- ❌ `MATCH` operator queries
- ❌ FTS5 tokenization configuration
- ❌ ~300 lines of FTS5 query preparation code
- ❌ Trigram generation and prefix handling

**KEEP (Standard SQL):**
- ✅ `SELECT * FROM entities WHERE permalink = :permalink`
- ✅ `SELECT * FROM entities WHERE permalink GLOB :pattern`
- ✅ `SELECT * FROM entities WHERE title LIKE :title`
- ✅ `SELECT * FROM entities WHERE json_extract(entity_metadata, ...) = :value`
- ✅ All date filtering, pagination, sorting
- ✅ Entity table structure and indexes

**ADD (ChromaDB):**
- ✅ ChromaDB persistent client (embedded)
- ✅ Semantic vector search
- ✅ Metadata filtering in ChromaDB
- ✅ Smart routing logic

## How (High Level)

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ FOSS Deployment (Local)                                      │
├─────────────────────────────────────────────────────────────┤
│ SQLite (data) + ChromaDB embedded (search)                   │
│ - No external services                                       │
│ - Local embedding model (sentence-transformers)              │
│ - Persists in ~/.basic-memory/chroma_data/                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Cloud Deployment (Multi-tenant)                              │
├─────────────────────────────────────────────────────────────┤
│ PostgreSQL/Neon (data) + ChromaDB server (search)           │
│ - Neon serverless Postgres for persistence                   │
│ - ChromaDB server in Docker container                        │
│ - Optional: OpenAI embeddings for better quality             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 1: ChromaDB Integration (2-3 days)

#### 1. Add ChromaDB Dependency
```toml
# pyproject.toml
dependencies = [
    "chromadb>=0.4.0",
    "sentence-transformers>=2.2.0",  # Local embeddings
]
```

#### 2. Create ChromaSearchBackend
```python
# src/basic_memory/search/chroma_backend.py
from chromadb import PersistentClient
from chromadb.utils import embedding_functions

class ChromaSearchBackend:
    def __init__(
        self,
        persist_directory: Path,
        collection_name: str = "knowledge_base",
        embedding_model: str = "all-MiniLM-L6-v2"
    ):
        """Initialize ChromaDB with local embeddings."""
        self.client = PersistentClient(path=str(persist_directory))

        # Use local sentence-transformers model (no API costs)
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"}  # Similarity metric
        )

    async def index_entity(self, entity: Entity):
        """Index entity with automatic embeddings."""
        # Combine title and content for semantic search
        document = self._format_document(entity)

        self.collection.upsert(
            ids=[f"entity_{entity.id}_{entity.project_id}"],
            documents=[document],
            metadatas=[{
                "entity_id": entity.id,
                "project_id": entity.project_id,
                "permalink": entity.permalink,
                "file_path": entity.file_path,
                "entity_type": entity.entity_type,
                "type": "entity",
            }]
        )

    async def search(
        self,
        query_text: str,
        project_id: int,
        limit: int = 10,
        filters: dict = None
    ) -> List[SearchResult]:
        """Semantic search with metadata filtering."""
        where = {"project_id": project_id}
        if filters:
            where.update(filters)

        results = self.collection.query(
            query_texts=[query_text],
            n_results=limit,
            where=where
        )

        return self._format_results(results)
```

#### 3. Update SearchRepository
```python
# src/basic_memory/repository/search_repository.py
class SearchRepository:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        project_id: int,
        chroma_backend: ChromaSearchBackend
    ):
        self.session_maker = session_maker
        self.project_id = project_id
        self.chroma = chroma_backend

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        # ... other filters
    ) -> List[SearchIndexRow]:
        """Search using ChromaDB for text, SQL for exact lookups."""

        # For exact permalink/pattern matches, use SQL
        if permalink or permalink_match:
            return await self._sql_exact_search(...)

        # For text search, use ChromaDB semantic search
        if search_text:
            results = await self.chroma.search(
                query_text=search_text,
                project_id=self.project_id,
                limit=limit,
                filters=self._build_filters(types, after_date, ...)
            )
            return results

        # Fallback to listing all
        return await self._list_entities(...)
```

#### 4. Update SearchService
```python
# src/basic_memory/services/search_service.py
class SearchService:
    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
        file_service: FileService,
        chroma_backend: ChromaSearchBackend,
    ):
        self.repository = search_repository
        self.entity_repository = entity_repository
        self.file_service = file_service
        self.chroma = chroma_backend

    async def index_entity(self, entity: Entity):
        """Index entity in ChromaDB."""
        if entity.is_markdown:
            await self._index_entity_markdown(entity)
        else:
            await self._index_entity_file(entity)

    async def _index_entity_markdown(self, entity: Entity):
        """Index markdown entity with full content."""
        # Index entity
        await self.chroma.index_entity(entity)

        # Index observations (as separate documents)
        for obs in entity.observations:
            await self.chroma.index_observation(obs, entity)

        # Index relations (metadata only)
        for rel in entity.outgoing_relations:
            await self.chroma.index_relation(rel, entity)
```

### Phase 2: PostgreSQL Support (1 day)

#### 1. Add PostgreSQL Database Type
```python
# src/basic_memory/db.py
class DatabaseType(Enum):
    MEMORY = auto()
    FILESYSTEM = auto()
    POSTGRESQL = auto()  # NEW

    @classmethod
    def get_db_url(cls, db_path_or_url: str, db_type: "DatabaseType") -> str:
        if db_type == cls.POSTGRESQL:
            return db_path_or_url  # Neon connection string
        elif db_type == cls.MEMORY:
            return "sqlite+aiosqlite://"
        return f"sqlite+aiosqlite:///{db_path_or_url}"
```

#### 2. Update Connection Handling
```python
def _create_engine_and_session(...):
    db_url = DatabaseType.get_db_url(db_path_or_url, db_type)

    if db_type == DatabaseType.POSTGRESQL:
        # Use asyncpg driver for Postgres
        engine = create_async_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # Health checks
        )
    else:
        # SQLite configuration
        engine = create_async_engine(db_url, connect_args=connect_args)

        # Only configure SQLite-specific settings for SQLite
        if db_type != DatabaseType.MEMORY:
            @event.listens_for(engine.sync_engine, "connect")
            def enable_wal_mode(dbapi_conn, connection_record):
                _configure_sqlite_connection(dbapi_conn, enable_wal=True)

    return engine, async_sessionmaker(engine, expire_on_commit=False)
```

#### 3. Remove SQLite-Specific Code
```python
# Remove from scoped_session context manager:
# await session.execute(text("PRAGMA foreign_keys=ON"))  # DELETE

# PostgreSQL handles foreign keys by default
```

### Phase 3: Migration & Testing (1-2 days)

#### 1. Create Migration Script
```python
# scripts/migrate_to_chromadb.py
async def migrate_fts5_to_chromadb():
    """One-time migration from FTS5 to ChromaDB."""
    # 1. Read all entities from database
    entities = await entity_repository.find_all()

    # 2. Index in ChromaDB
    for entity in entities:
        await search_service.index_entity(entity)

    # 3. Drop FTS5 table (Alembic migration)
    await session.execute(text("DROP TABLE IF EXISTS search_index"))
```

#### 2. Update Tests
- Replace FTS5 test fixtures with ChromaDB fixtures
- Test semantic search quality
- Test multi-project isolation in ChromaDB
- Benchmark performance vs FTS5

#### 3. Documentation Updates
- Update search documentation
- Add ChromaDB configuration guide
- Document embedding model options
- PostgreSQL deployment guide

### Configuration

```python
# config.py
class BasicMemoryConfig:
    # Database
    database_type: DatabaseType = DatabaseType.FILESYSTEM
    database_path: Path = Path.home() / ".basic-memory" / "memory.db"
    database_url: Optional[str] = None  # For Postgres: postgresql://...

    # Search
    chroma_persist_directory: Path = Path.home() / ".basic-memory" / "chroma_data"
    embedding_model: str = "all-MiniLM-L6-v2"  # Local model
    embedding_provider: str = "local"  # or "openai"
    openai_api_key: Optional[str] = None  # For cloud deployments
```

### Deployment Configurations

#### Local (FOSS)
```yaml
# Default configuration
database_type: FILESYSTEM
database_path: ~/.basic-memory/memory.db
chroma_persist_directory: ~/.basic-memory/chroma_data
embedding_model: all-MiniLM-L6-v2
embedding_provider: local
```

#### Cloud (Docker Compose)
```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: basic_memory
      POSTGRES_PASSWORD: ${DB_PASSWORD}

  chromadb:
    image: chromadb/chroma:latest
    volumes:
      - chroma_data:/chroma/chroma
    environment:
      ALLOW_RESET: true

  app:
    environment:
      DATABASE_TYPE: POSTGRESQL
      DATABASE_URL: postgresql://postgres:${DB_PASSWORD}@postgres/basic_memory
      CHROMA_HOST: chromadb
      CHROMA_PORT: 8000
      EMBEDDING_PROVIDER: local  # or openai
```

## How to Evaluate

### Success Criteria

#### Functional Requirements
- ✅ Semantic search finds related concepts (e.g., "AI" finds "machine learning")
- ✅ Exact permalink/pattern matches work (e.g., `specs/*`)
- ✅ Multi-project isolation maintained
- ✅ All existing search filters work (type, date, metadata)
- ✅ MCP tools continue to work without changes
- ✅ Works with both SQLite and PostgreSQL

#### Performance Requirements
- ✅ Search latency < 200ms for 1000 documents (local embedding)
- ✅ Indexing time comparable to FTS5 (~10 files/sec)
- ✅ Initial sync time not significantly worse than current
- ✅ Memory footprint < 1GB for local deployments

#### Quality Requirements
- ✅ Better search relevance than FTS5 keyword matching
- ✅ Handles typos and word variations
- ✅ Finds semantically similar content

#### Deployment Requirements
- ✅ FOSS: Works out-of-box with no external services
- ✅ Cloud: Integrates with PostgreSQL (Neon)
- ✅ No breaking changes to MCP API
- ✅ Migration script for existing users

### Testing Procedure

#### 1. Unit Tests
```bash
# Test ChromaDB backend
pytest tests/test_chroma_backend.py

# Test search repository with ChromaDB
pytest tests/test_search_repository.py

# Test search service
pytest tests/test_search_service.py
```

#### 2. Integration Tests
```bash
# Test full search workflow
pytest test-int/test_search_integration.py

# Test with PostgreSQL
DATABASE_TYPE=POSTGRESQL pytest test-int/
```

#### 3. Semantic Search Quality Tests
```python
# Test semantic similarity
search("machine learning") should find:
- "neural networks"
- "deep learning"
- "AI algorithms"

search("software architecture") should find:
- "system design"
- "design patterns"
- "microservices"
```

#### 4. Performance Benchmarks
```bash
# Run search benchmarks
pytest test-int/test_search_performance.py -v

# Measure:
- Search latency (should be < 200ms)
- Indexing throughput (should be ~10 files/sec)
- Memory usage (should be < 1GB)
```

#### 5. Migration Testing
```bash
# Test migration from FTS5 to ChromaDB
python scripts/migrate_to_chromadb.py

# Verify all entities indexed
# Verify search results quality
# Verify no data loss
```

### Metrics

**Search Quality:**
- Semantic relevance score (manual evaluation)
- Precision/recall for common queries
- User satisfaction (qualitative)

**Performance:**
- Average search latency (ms)
- P95/P99 search latency
- Indexing throughput (files/sec)
- Memory usage (MB)

**Deployment:**
- Local deployment success rate
- Cloud deployment success rate
- Migration success rate

## Implementation Checklist

### Phase 1: ChromaDB Integration
- [ ] Add ChromaDB and sentence-transformers dependencies
- [ ] Create ChromaSearchBackend class
- [ ] Update SearchRepository to use ChromaDB
- [ ] Update SearchService indexing methods
- [ ] Remove FTS5 table creation code
- [ ] Update search query logic
- [ ] Add ChromaDB configuration to BasicMemoryConfig

### Phase 2: PostgreSQL Support
- [ ] Add DatabaseType.POSTGRESQL enum
- [ ] Update get_db_url() for Postgres connection strings
- [ ] Add asyncpg dependency
- [ ] Update engine creation for Postgres
- [ ] Remove SQLite-specific PRAGMA statements
- [ ] Test with Neon database

### Phase 3: Testing & Migration
- [ ] Write unit tests for ChromaSearchBackend
- [ ] Update search integration tests
- [ ] Add semantic search quality tests
- [ ] Create performance benchmarks
- [ ] Write migration script from FTS5
- [ ] Test migration with existing data
- [ ] Update documentation

### Phase 4: Deployment
- [ ] Update docker-compose.yml for cloud
- [ ] Document local FOSS deployment
- [ ] Document cloud PostgreSQL deployment
- [ ] Create migration guide for users
- [ ] Update MCP tool documentation

## Notes

### Embedding Model Trade-offs

**Local Model: `all-MiniLM-L6-v2`**
- Size: 80MB download
- Speed: ~50ms embedding time
- Dimensions: 384
- Cost: $0
- Quality: Good for general knowledge
- Best for: FOSS deployments

**OpenAI: `text-embedding-3-small`**
- Speed: ~100-200ms (API call)
- Dimensions: 1536
- Cost: ~$0.13 per 1M tokens (~$0.01 per 1000 notes)
- Quality: Excellent
- Best for: Cloud deployments with budget

### ChromaDB Storage

ChromaDB stores data in:
```
~/.basic-memory/chroma_data/
  ├── chroma.sqlite3        # Metadata
  ├── index/                # HNSW indexes
  └── collections/          # Vector data
```

Typical sizes:
- 100 notes: ~5MB
- 1000 notes: ~50MB
- 10000 notes: ~500MB

### Why Not Keep FTS5?

**Considered:** Hybrid approach (FTS5 for SQLite + tsvector for Postgres)
**Rejected because:**
- 2x the code to maintain
- 2x the tests to write
- 2x the bugs to fix
- Inconsistent search behavior between deployments
- ChromaDB provides better search quality anyway

**ChromaDB wins:**
- One implementation for both databases
- Better search quality (semantic!)
- Database-agnostic architecture
- Embedded mode for FOSS (no servers needed)

## implementation

  Proposed Architecture

  Option 1: ChromaDB Only (Simplest)

  class ChromaSearchBackend:
      def __init__(self, path: str, embedding_model: str = "all-MiniLM-L6-v2"):yes
          # For local: embedded client (no server!)
          self.client = chromadb.PersistentClient(path=path)

          # Use local embedding model (no API costs!)
          from chromadb.utils import embedding_functions
          self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
              model_name=embedding_model
          )

          self.collection = self.client.get_or_create_collection(
              name="knowledge_base",
              embedding_function=self.embed_fn
          )

      async def index_entity(self, entity: Entity):
          # ChromaDB handles embeddings automatically!
          self.collection.upsert(
              ids=[str(entity.id)],
              documents=[f"{entity.title}\n{entity.content}"],
              metadatas=[{
                  "permalink": entity.permalink,
                  "type": entity.entity_type,
                  "file_path": entity.file_path
              }]
          )

      async def search(self, query: str, filters: dict = None):
          # Semantic search with optional metadata filters
          results = self.collection.query(
              query_texts=[query],
              n_results=10,
              where=filters  # e.g., {"type": "note"}
          )
          return results

  Deployment:
  - Local (FOSS): ChromaDB embedded, local embedding model, NO servers
  - Cloud: ChromaDB server OR still embedded (it's just a Python lib!)

  Option 2: Hybrid FTS + ChromaDB (Best UX)

  class HybridSearchBackend:
      def __init__(self):
          self.fts = SQLiteFTS5Backend()    # Fast keyword search
          self.chroma = ChromaSearchBackend()  # Semantic search

      async def search(self, query: str, search_type: str = "auto"):
          if search_type == "exact":
              # User wants exact match: "specs/search-feature"
              return await self.fts.search(query)

          elif search_type == "semantic":
              # User wants related concepts
              return await self.chroma.search(query)

          else:  # "auto"
              # Check if query looks like exact match
              if "/" in query or query.startswith('"'):
                  return await self.fts.search(query)

              # Otherwise use semantic search
              return await self.chroma.search(query)

  Embedding Options

  Option A: Local Model (FREE, FOSS-friendly)

  # Uses sentence-transformers (runs locally)
  # Model: ~100MB download
  # Speed: ~50-100ms for embedding
  # Cost: $0

  from chromadb.utils import embedding_functions
  embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
      model_name="all-MiniLM-L6-v2"  # Fast, accurate, free
  )

  Option B: OpenAI Embeddings (Cloud only)

  # For cloud users who want best quality
  # Model: text-embedding-3-small
  # Speed: ~100-200ms via API
  # Cost: ~$0.13 per 1M tokens (~$0.01 per 1000 notes)

  embed_fn = embedding_functions.OpenAIEmbeddingFunction(
      api_key="...",
      model_name="text-embedding-3-small"
  )

  Performance Comparison

  Local embedding model: all-MiniLM-L6-v2
  Embedding time: ~50ms per note
  Search time: ~100ms for 1000 notes
  Memory: ~500MB (model + ChromaDB)
  Cost: $0
  Quality: Good (384 dimensions)

  OpenAI embeddings: text-embedding-3-small
  Embedding time: ~100-200ms per note (API call)
  Search time: ~50ms for 1000 notes
  Cost: ~$0.01 per 1000 notes
  Quality: Excellent (1536 dimensions)

  My Recommendation: ChromaDB with Local Embeddings

  Here's the plan:

  Phase 1: Local ChromaDB (1-2 days)

  # FOSS version
  - SQLite for data persistence
  - ChromaDB embedded for semantic search
  - Local embedding model (no API costs)
  - NO external services required

  Benefits:
  - ✅ Same deployment as current (just Python package)
  - ✅ Semantic search for better UX
  - ✅ Free embeddings with local model
  - ✅ No servers needed

  Phase 2: Postgres + ChromaDB Cloud (1-2 days)

  # Cloud version
  - Postgres for data persistence
  - ChromaDB server for semantic search
  - OpenAI embeddings (higher quality)
  - OR keep local embeddings (cheaper)

  Phase 3: Hybrid Search (optional, 1 day)

  # Add FTS for exact matches alongside ChromaDB
  - Quick keyword search when needed
  - Semantic search for exploration
  - Best of both worlds

  Code Estimate

  Just ChromaDB (replacing FTS5):
  - Remove FTS5 code: 2 hours
  - Add ChromaDB backend: 4 hours
  - Update search service: 2 hours
  - Testing: 4 hours
  - Total: 1.5 days

  ChromaDB + Postgres migration:
  - Add Postgres support: 4 hours
  - Test with Neon: 2 hours
  - Total: +0.75 days

  Grand total: 2-3 days for complete migration

  The Kicker

  ChromaDB solves BOTH problems:
  1. ✅ Works with SQLite AND Postgres (it's separate!)
  2. ✅ No server needed for local (embedded mode)
  3. ✅ Better search than FTS5 (semantic!)
  4. ✅ One implementation for both deployments

  Want me to prototype this? I can show you:
  1. ChromaDB embedded with local embeddings
  2. Example searches showing semantic matching
  3. Performance benchmarks
  4. Migration from FTS5


## Observations

- [problem] SQLite FTS5 and PostgreSQL tsvector are incompatible architectures requiring dual implementation #database-compatibility
- [problem] Cloud deployments lose database on container restart requiring full re-sync #persistence
- [solution] ChromaDB provides database-agnostic semantic search eliminating dual implementation #architecture
- [advantage] Semantic search finds related concepts beyond keyword matching improving UX #search-quality
- [deployment] Embedded ChromaDB requires no external services for FOSS #simplicity
- [migration] Moving to PostgreSQL solves cloud persistence issues #cloud-architecture
- [performance] Local embedding models provide good quality at zero cost #cost-optimization
- [trade-off] Embedding generation adds ~50ms latency vs instant FTS5 indexing #performance
- [benefit] Single search codebase reduces maintenance burden and test coverage needs #maintainability

## Prior Art / References

### Community Fork: manuelbliemel/basic-memory (feature/vector-search)

**Repository**: https://github.com/manuelbliemel/basic-memory/tree/feature/vector-search

**Key Implementation Details**:

**Vector Database**: ChromaDB (same as our approach!)

**Embedding Models**:
- Local: `all-MiniLM-L6-v2` (default, 384 dims) - same model we planned
- Also supports: `all-mpnet-base-v2`, `paraphrase-MiniLM-L6-v2`, `multi-qa-MiniLM-L6-cos-v1`
- OpenAI: `text-embedding-ada-002`, `text-embedding-3-small`, `text-embedding-3-large`

**Chunking Strategy** (interesting - we didn't consider this):
- Chunk Size: 500 characters
- Chunk Overlap: 50 characters
- Breaks documents into smaller pieces for better semantic search

**Search Strategies**:
1. `fuzzy_only` (default) - FTS5 only
2. `vector_only` - ChromaDB only
3. `hybrid` (recommended) - Both FTS5 + ChromaDB
4. `fuzzy_primary` - FTS5 first, ChromaDB fallback
5. `vector_primary` - ChromaDB first, FTS5 fallback

**Configuration**:
- Similarity Threshold: 0.1
- Max Results: 5
- Storage: `~/.basic-memory/chroma/`
- Config: `~/.basic-memory/config.json`

**Key Differences from Our Approach**:

| Aspect | Their Approach | Our Approach |
|--------|---------------|--------------|
| FTS5 | Keep FTS5 + add ChromaDB | Remove FTS5, use SQL for exact lookups |
| Search Strategy | 5 configurable strategies | Smart routing (automatic) |
| Document Processing | Chunk into 500-char pieces | Index full documents |
| Hybrid Mode | Run both, merge, dedupe | Route to best backend |
| Configuration | User-configurable strategy | Automatic based on query type |

**What We Can Learn**:

1. **Chunking**: Breaking documents into 500-character chunks with 50-char overlap may improve semantic search quality for long documents
   - Pro: Better granularity for semantic matching
   - Con: More vectors to store and search
   - Consider: Optional chunking for large documents (>2000 chars)

2. **Configurable Strategies**: Allowing users to choose search strategy provides flexibility
   - Pro: Power users can tune behavior
   - Con: More complexity, most users won't configure
   - Consider: Default to smart routing, allow override via config

3. **Similarity Threshold**: They use 0.1 as default
   - Consider: Benchmark different thresholds for quality

4. **Storage Location**: `~/.basic-memory/chroma/` matches our planned `chroma_data/` approach

**Potential Collaboration**:
- Their implementation is nearly complete as a fork
- Could potentially merge their work or use as reference implementation
- Their chunking strategy could be valuable addition to our approach

## Relations

- implements [[SPEC-11 Basic Memory API Performance Optimization]]
- relates_to [[Performance Optimizations Documentation]]
- enables [[PostgreSQL Migration]]
- improves_on [[SQLite FTS5 Search]]
- references [[manuelbliemel/basic-memory feature/vector-search fork]]
