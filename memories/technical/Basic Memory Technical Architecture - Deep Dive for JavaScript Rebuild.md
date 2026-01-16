---
title: Basic Memory Technical Architecture - Deep Dive for JavaScript Rebuild
type: note
permalink: technical/basic-memory-technical-architecture-deep-dive-for-java-script-rebuild
tags:
- architecture,technical,javascript,rebuild,database
---

A comprehensive technical analysis of Basic Memory's architecture, data flow, and considerations for rebuilding in JavaScript.

## High-Level Architecture

Basic Memory follows a **local-first, file-as-source-of-truth** architecture with SQLite for indexing and search.

### Core Data Flow

```
Markdown Files (source of truth)
        ↓
    Checksum Detection
        ↓
    Parse Markdown → Extract Structure
    ├─ Frontmatter (YAML)
    ├─ Observations: - [category] content
    └─ Relations: - type [[Target]]
        ↓
    Write to SQLite
    ├─ Entity table (main record)
    ├─ Observation table (atomic facts)
    └─ Relation table (knowledge graph edges)
        ↓
    Index in FTS5 (Full-Text Search)
    └─ BM25 ranking for relevance
        ↓
    Query via MCP Tools or API
```

## Database Schema

### Tables and Relationships

- [schema] **Entity** - Main content nodes (notes, guides, etc.)
- [schema] **Observation** - Semantic facts extracted from `- [category] content` patterns
- [schema] **Relation** - Directed edges between entities via `[[WikiLinks]]`
- [schema] **Project** - Isolated workspaces (multi-tenant)
- [schema] **search_index** - FTS5 virtual table for full-text search

### Entity Table Structure

```sql
CREATE TABLE entity (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    entity_type TEXT DEFAULT 'note',
    content_type TEXT DEFAULT 'text/markdown',
    permalink TEXT NOT NULL,              -- URL-safe identifier
    file_path TEXT NOT NULL,              -- Relative path in project
    checksum TEXT,                        -- SHA256 for change detection
    project_id INTEGER NOT NULL,          -- FK to project
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    
    UNIQUE(permalink, project_id) WHERE content_type = 'text/markdown',
    UNIQUE(file_path, project_id)
)
```

- [detail] Permalink is unique per project, generated from file path
- [detail] Checksum enables fast change detection without re-parsing
- [detail] Conditional unique constraint only for markdown (allows duplicates for other types)

### Observation Table Structure

```sql
CREATE TABLE observation (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL,           -- FK to entity
    content TEXT NOT NULL,                -- The observation text
    category TEXT DEFAULT 'note',         -- Extracted from [category]
    context TEXT,                         -- Optional context
    tags JSON,                            -- Array of hashtags
    created_at TIMESTAMP,
    
    FOREIGN KEY(entity_id) REFERENCES entity(id) ON DELETE CASCADE
)
```

- [pattern] Observations are extracted via regex: `^\[([^\[\]()]+)\]\s+(.+)`
- [pattern] Synthetic permalink: `{entity.permalink}/observations/{category}/{content-slug}`
- [indexing] Indexed on `entity_id` and `category` for fast queries

### Relation Table Structure

```sql
CREATE TABLE relation (
    id INTEGER PRIMARY KEY,
    from_id INTEGER NOT NULL,             -- Source entity
    to_id INTEGER,                        -- Target entity (nullable for unresolved)
    to_name TEXT,                         -- Human name when unresolved
    relation_type TEXT DEFAULT 'relates_to',
    context TEXT,
    
    FOREIGN KEY(from_id) REFERENCES entity(id) ON DELETE CASCADE,
    FOREIGN KEY(to_id) REFERENCES entity(id) ON DELETE CASCADE,
    
    UNIQUE(from_id, to_id, relation_type),
    UNIQUE(from_id, to_name, relation_type)
)
```

- [pattern] Unresolved relations have `to_id = NULL` and `to_name` populated
- [pattern] Background resolution matches `to_name` against existing entities
- [pattern] Supports bidirectional graph traversal via `from_id` and `to_id` indexes

### FTS5 Virtual Table (Full-Text Search)

```sql
CREATE VIRTUAL TABLE search_index USING fts5(
    id UNINDEXED,
    title,                                -- Searchable
    content_stems,                        -- Tokenized content (main search)
    content_snippet,                      -- First 500 chars for display
    permalink,                            -- Path-based search
    file_path UNINDEXED,
    type UNINDEXED,                       -- entity|relation|observation
    
    -- Type-specific fields
    from_id UNINDEXED,
    to_id UNINDEXED,
    relation_type UNINDEXED,
    entity_id UNINDEXED,
    category UNINDEXED,
    
    metadata UNINDEXED,                   -- JSON blob
    project_id UNINDEXED,
    created_at UNINDEXED,
    updated_at UNINDEXED,
    
    tokenize='unicode61 tokenchars 0x2F', -- "/" is token char
    prefix='1,2,3,4'                      -- Enable 1-4 char prefix search
)
```

- [optimization] FTS5 is SQLite's latest full-text search engine (better than FTS3/4)
- [optimization] BM25 scoring ranks results by relevance
- [optimization] `content_stems` tokenized by whitespace for efficient search
- [optimization] `prefix='1,2,3,4'` enables prefix matching (e.g., "doc" matches "document")
- [gotcha] `tokenchars 0x2F` treats "/" as part of token for path search

## Markdown Parsing Pipeline

### Frontmatter Extraction

- [tool] Uses `python-frontmatter` library
- [format] YAML between `---` delimiters at top of file
- [fields] title, type, tags, permalink, custom metadata

```markdown
---
title: My Note
type: guide
tags: ["javascript", "tutorial"]
permalink: custom-permalink
---

Content here...
```

### Observation Extraction

- [pattern] Regex: `^\s*-\s*\[([^\[\]()]+)\]\s+(.+?)(?:\s+#(\w+))*(?:\s+\(([^)]+)\))?$`
- [format] `- [category] content #tag1 #tag2 (optional context)`

**Examples:**
```markdown
- [concept] React Hooks enable state in function components
- [benefit] Reduces bundle size #performance
- [gotcha] Cannot use hooks in conditions or loops (React rules)
```

**Extracted Structure:**
```javascript
{
  category: "concept" | "benefit" | "gotcha",
  content: "React Hooks enable state...",
  tags: ["performance"],
  context: "React rules"
}
```

### Relation Extraction

- [pattern] Explicit: `^\s*-\s*([^[\]]+)\s*\[\[([^\]]+)\]\](?:\s+\(([^)]+)\))?$`
- [pattern] Inline: Any `[[target]]` in content

**Examples:**
```markdown
- related_to [[React]]
- implements [[Server Components]]
- depends_on [[Node.js]] (runtime requirement)

Some text with [[inline reference]] to another note.
```

**Extracted Structure:**
```javascript
{
  type: "related_to" | "implements" | "depends_on" | "relates_to",
  target: "React" | "Server Components",
  context: "runtime requirement"
}
```

- [gotcha] Nested `[[` needs depth tracking to prevent malformed targets
- [gotcha] Inline relations default to type "relates_to"

## Sync Process (File System ↔ Database)

### Change Detection

- [algorithm] Checksum-based detection (SHA256 of file content)
- [optimization] Only re-parse files with changed checksums
- [optimization] Move detection via matching checksums with different paths

```python
# Pseudo-algorithm
checksums_on_disk = scan_directory()
checksums_in_db = query_database()

new_files = disk - db
deleted_files = db - disk
modified_files = {f for f in (disk ∩ db) if checksum_changed(f)}
moved_files = detect_moves(checksums_on_disk, checksums_in_db)
```

### Sync Order (Important!)

1. **Moves** - Update file_path, keep entity_id (preserves relations)
2. **Deletes** - Remove entities (cascade to observations/relations)
3. **New** - Create entities + parse content
4. **Modified** - Update entities + re-parse content

- [gotcha] Order matters for relation resolution
- [gotcha] Must process moves before deletes to avoid cascade

### Relation Resolution

- [algorithm] Two-pass approach
  1. First pass: Create relations with `to_id = NULL` for unknown targets
  2. Second pass: Resolve `to_name` against entities in project

```javascript
// Pseudo-code
unresolved = relations.filter(r => r.to_id === null)
for (const rel of unresolved) {
  const target = findEntity({ 
    title: rel.to_name, 
    project_id: rel.project_id 
  })
  if (target) {
    rel.to_id = target.id
  }
}
```

## Search Implementation

### Query Preparation

- [security] Escape special FTS5 characters: `"*()^`
- [optimization] Add `*` suffix for prefix matching (unless exact path)
- [optimization] Wrap multi-word terms in quotes: `"exact phrase"*`

**Examples:**
```javascript
// Input: "react hooks"
// FTS5: "react hooks"*

// Input: "testing AND debugging"
// FTS5: testing AND debugging  (preserve boolean operators)

// Input: "/path/to/file.md"
// FTS5: /path/to/file.md  (no wildcard for paths)
```

### Search Modes

1. **Exact permalink** - Direct lookup via index
2. **Glob pattern** - SQLite GLOB for `path/*` patterns
3. **Full-text** - FTS5 with BM25 ranking
4. **Filtered** - By type, date range, project

### BM25 Scoring

- [algorithm] SQLite's built-in `bm25(search_index)` function
- [scoring] Lower scores = better matches
- [ranking] Considers term frequency and document length
- [gotcha] Score is negative (more negative = more relevant)

## Repository Pattern

### Why Repository Pattern?

- [architecture] Separates data access from business logic
- [architecture] Enables testing with mock repositories
- [architecture] Centralizes query optimization and caching

### Key Repositories

1. **EntityRepository** - CRUD for entities + permalink resolution
2. **ObservationRepository** - Query observations by category/entity
3. **RelationRepository** - Graph traversal and relation resolution
4. **SearchRepository** - FTS5 query building and execution

## JavaScript Rebuild Considerations

### Easy in JavaScript

- [advantage] **Markdown parsing** - Excellent libraries (remark, unified ecosystem)
- [advantage] **Async/await** - Native promises (Python has bolted-on async)
- [advantage] **JSON handling** - First-class citizen vs Python's dict serialization
- [advantage] **Regex** - Similar capabilities to Python
- [advantage] **File watching** - chokidar is excellent (better than Python's watchdog)

### Challenging in JavaScript

- [challenge] **SQLite async** - better-sqlite3 is sync, need to use node-sqlite3 or sql.js
- [challenge] **FTS5** - Must ensure SQLite compiled with FTS5 support
- [challenge] **ORM** - TypeORM/Prisma/Sequelize more verbose than SQLAlchemy
- [challenge] **Migrations** - Alembic equivalent would be Knex.js or TypeORM migrations
- [challenge] **Checksum** - Node's crypto module works but less ergonomic than hashlib

### Critical JavaScript Gotchas

#### 1. SQLite FTS5 Availability

- [gotcha] Not all SQLite builds include FTS5
- [solution] Use `better-sqlite3` with `--build-from-source` flag
- [solution] Or bundle custom SQLite with FTS5 enabled

```bash
npm install better-sqlite3 --build-from-source
```

#### 2. Async File Operations

- [gotcha] Node.js fs operations are async by default
- [gotcha] Sync operations block event loop (bad for large file sets)
- [solution] Use fs.promises or fs/promises for clean async/await

```javascript
import { readFile, writeFile } from 'fs/promises'

const content = await readFile('note.md', 'utf-8')
await writeFile('note.md', newContent, 'utf-8')
```

#### 3. Path Normalization

- [gotcha] Windows vs Unix path separators (`\` vs `/`)
- [gotcha] Case-sensitive filesystems (macOS APFS vs Linux ext4)
- [solution] Use `path.posix` for consistent `/` separators
- [solution] Store normalized paths in database

```javascript
import path from 'path'

// Always use posix for database storage
const normalized = path.posix.normalize(filePath)
```

#### 4. Unicode Normalization

- [gotcha] macOS uses NFD (decomposed), Linux uses NFC (composed)
- [gotcha] "café" can be stored as `cafe\u0301` or `caf\u00E9`
- [solution] Normalize to NFC before database operations

```javascript
const normalized = filename.normalize('NFC')
```

#### 5. JSON in SQLite

- [gotcha] SQLite has JSON1 extension (may not be enabled)
- [gotcha] JSON queries less efficient than dedicated fields
- [solution] Enable JSON1 extension at compile time
- [solution] Or serialize/deserialize in application layer

```javascript
// Application-level JSON handling
const tags = JSON.parse(row.tags || '[]')
const metadata = JSON.parse(row.metadata || '{}')
```

#### 6. Permalink Conflicts

- [gotcha] Must handle concurrent writes (race conditions)
- [gotcha] Unique constraint violations need retry logic
- [solution] Use database transactions
- [solution] Implement retry with exponential backoff

```javascript
async function resolvePermalink(base, project_id, attempt = 0) {
  const candidate = attempt === 0 ? base : `${base}-${attempt}`
  try {
    await db.insert({ permalink: candidate, project_id })
    return candidate
  } catch (err) {
    if (err.code === 'SQLITE_CONSTRAINT') {
      return resolvePermalink(base, project_id, attempt + 1)
    }
    throw err
  }
}
```

### Recommended JavaScript Stack

- [recommendation] **Database** - better-sqlite3 (sync, fast) or node-sqlite3 (async)
- [recommendation] **ORM** - Drizzle ORM (lightweight, TypeScript-first)
- [recommendation] **Markdown** - remark + unified ecosystem
- [recommendation] **Frontmatter** - gray-matter package
- [recommendation] **File watching** - chokidar
- [recommendation] **Checksums** - Node.js crypto.createHash('sha256')
- [recommendation] **Path handling** - path.posix for consistency
- [recommendation] **MCP** - @modelcontextprotocol/sdk

### Performance Considerations

#### Python Advantages
- [performance] SQLAlchemy lazy loading prevents N+1 queries
- [performance] Asyncio good for I/O-bound operations
- [performance] C extensions for crypto (hashlib)

#### JavaScript Advantages
- [performance] V8 JIT compilation for hot paths
- [performance] better-sqlite3 uses native bindings (faster than Python)
- [performance] Non-blocking I/O by default

#### Benchmark Estimates
- [estimate] File parsing: JS ~10-20% faster (V8 optimization)
- [estimate] SQLite operations: Similar (both use native bindings)
- [estimate] Full-text search: Similar (SQLite FTS5 in both)
- [estimate] Concurrent operations: JS better (non-blocking I/O)

### TypeScript Benefits

- [benefit] Type safety for schema validation
- [benefit] Better IDE autocomplete for queries
- [benefit] Compile-time error detection
- [benefit] Self-documenting API interfaces

```typescript
interface Entity {
  id: number
  title: string
  permalink: string
  file_path: string
  checksum: string
  project_id: number
  created_at: Date
  updated_at: Date
}

interface Observation {
  id: number
  entity_id: number
  category: string
  content: string
  context?: string
  tags: string[]
}

interface Relation {
  id: number
  from_id: number
  to_id: number | null
  to_name: string
  relation_type: string
  context?: string
}
```

## Implementation Roadmap

### Phase 1: Core Data Layer
- [task] SQLite setup with FTS5
- [task] Schema creation (Entity, Observation, Relation, Project)
- [task] Repository pattern implementation
- [task] Migration system (Knex.js or TypeORM)

### Phase 2: Markdown Pipeline
- [task] Frontmatter parsing (gray-matter)
- [task] Observation extraction (regex + remark plugin)
- [task] Relation extraction (regex + remark plugin)
- [task] Checksum calculation

### Phase 3: Sync Engine
- [task] File system scanning
- [task] Change detection (checksum comparison)
- [task] Sync orchestration (moves → deletes → new → modified)
- [task] Relation resolution

### Phase 4: Search
- [task] FTS5 index management
- [task] Query preparation (escape, wildcards, boolean operators)
- [task] BM25 ranking
- [task] Search result formatting

### Phase 5: MCP Integration
- [task] MCP server setup (@modelcontextprotocol/sdk)
- [task] write_note tool
- [task] read_note tool
- [task] search_notes tool
- [task] build_context tool

### Phase 6: Optimization
- [task] Connection pooling
- [task] Query caching
- [task] Incremental indexing
- [task] Background sync

## Testing Strategy

### Unit Tests
- [test] Markdown parsing (fixtures with known outputs)
- [test] Permalink generation and conflict resolution
- [test] Checksum calculation
- [test] Observation/Relation extraction

### Integration Tests
- [test] Full sync cycle (create → modify → delete)
- [test] Relation resolution
- [test] Search queries with various patterns
- [test] Move detection

### Performance Tests
- [test] Large file set (1000+ notes)
- [test] Search performance (1000+ entities)
- [test] Concurrent operations
- [test] Memory usage during sync

## Security Considerations

- [security] Path traversal prevention (validate `../` in file paths)
- [security] SQL injection prevention (parameterized queries)
- [security] FTS5 query injection (escape special characters)
- [security] File system access (sandboxing to project directory)

## Migration from Python Version

- [migration] Export SQLite database as JSON
- [migration] Re-sync from markdown files (source of truth)
- [migration] Verify checksum matches
- [migration] Compare search results between implementations

## Key Architectural Insights

- [insight] **Files are source of truth** - Database is a cache/index
- [insight] **Two-pass parsing** - First structure, then relations
- [insight] **Permalink stability** - Once assigned, never changes (even on title change)
- [insight] **Project isolation** - All queries filtered by project_id
- [insight] **Unresolved relations** - Gracefully handle references to non-existent entities
- [insight] **Checksum-based sync** - Fast change detection without re-parsing

## Relations

- related_to [[Basic Memory]]
- related_to [[SQLite]]
- related_to [[FTS5]]
- related_to [[JavaScript]]
- related_to [[TypeScript]]
- alternative_to [[Python Implementation]]