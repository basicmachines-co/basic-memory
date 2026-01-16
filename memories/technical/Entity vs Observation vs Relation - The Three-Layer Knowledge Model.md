# Entity vs Observation vs Relation - The Three-Layer Knowledge Model

Basic Memory uses a graph-based knowledge representation that separates three distinct but interconnected concepts to create a powerful, searchable knowledge graph.

## The Three Core Concepts

### Entity = The "Thing" (Node in the Graph)

## Observations

- [definition] Entity is a discrete concept, document, or idea represented as a markdown file
- [architecture] Entity serves as the node in your knowledge graph
- [schema] Entity table stores id, title, permalink, file_path, checksum, and entity_type
- [example] A markdown file titled "React Hooks" becomes an entity in the system
- [purpose] Entities are the top-level containers for knowledge

### Observation = Facts About the Thing (Attributes)

- [definition] Observation is a categorized fact about an entity extracted from `- [category] content` syntax
- [architecture] Observations live inside the entity's file but are stored separately in the database
- [schema] Observation table stores id, entity_id, category, content, tags, and context
- [power] Each observation becomes independently searchable rather than searching whole documents
- [example] `- [concept] useState manages component state` creates a searchable observation
- [example] `- [tip] Use useCallback to prevent re-renders (performance)` extracts performance context
- [advantage] When searching "prevent re-renders" you find the specific tip, not the entire document
- [multiplier] One note with 10 observations creates 10+ searchable entries in the index

### Relation = Connection Between Things (Edges)

- [definition] Relation is a directional link between two entities
- [extraction] Relations extracted from `- relation_type [[Target]]` or inline `[[WikiLinks]]`
- [architecture] Relations are the edges in your knowledge graph
- [schema] Relation table stores id, from_id, to_id, relation_type, and context
- [feature] to_id can be NULL for forward references that resolve when target is created
- [example] `- implements [[React API]]` creates a typed relation
- [example] `You can also use [[inline references]]` creates untyped relations
- [navigation] Relations enable graph traversal from React Hooks → React API → JavaScript → Closures
- [query] Can query "Show me everything that implements React API"

## Why Three Separate Tables?

### 1. Granular Search

- [benefit] Single entity creates multiple searchable entries for precision
- [example] One note with title + 3 observations + 2 relations creates 6 searchable entries
- [precision] Searching finds specific observations rather than entire documents
- [efficiency] Search returns the exact fact needed rather than forcing users to read entire documents

### 2. Knowledge Graph Navigation

- [capability] Relations create traversable paths through your knowledge
- [example] Navigate path: React Hooks → implements → React API → uses → JavaScript Closures
- [discovery] Graph traversal enables serendipitous knowledge discovery
- [context] Following relation chains builds understanding of how concepts connect

### 3. Flexible Querying

- [capability] Can query by category across entire knowledge base
- [example] `SELECT * FROM observation WHERE category = 'gotcha'` finds all gotchas
- [example] `SELECT * FROM relation WHERE relation_type = 'implements'` finds all implementations
- [analysis] Can analyze knowledge density: entities with >10 observations indicate rich content
- [aggregation] Can group and analyze observations by category or tag

### 4. Multi-Index BM25 Scoring

- [algorithm] Search queries all three tables simultaneously and ranks by relevance
- [strategy] Query "React state management" searches entity.title, entity.content_stems, observation.content, and relation.target
- [ranking] Results ranked by BM25 score with most specific matches first
- [precision] Multi-table search provides better precision than full-text search alone
- [example] Observation matches score higher than entity matches for specific queries

## Real-World Example

- [demonstration] A note about "Basic Memory Technical Architecture" illustrates all three concepts
- [entity-example] Title and overall content form the entity
- [observation-example] `- [architecture] Local-first with SQLite indexing` becomes searchable fact
- [observation-example] `- [gotcha] Unicode normalization differs on macOS vs Linux #cross-platform` tagged and searchable
- [relation-example] `- uses [[SQLite]]` creates graph edge to SQLite entity
- [multiplier-example] One note with 5 observations and 4 relations creates 10 index entries

## Search Scenarios

- [scenario] Searching "cross-platform issues" finds the specific gotcha observation
- [scenario] Querying "what uses SQLite" finds all relations pointing to SQLite
- [scenario] Searching "knowledge graph architecture" finds entity plus related-to relations
- [advantage] Atomic search units provide precision unavailable with full-document search

## The Power of Separation

- [principle] Decomposing knowledge into atomic queryable units enables better search
- [capability] Units can be searched independently for precision
- [capability] Units can be traversed as a graph for discovery
- [capability] Units can be aggregated by category for analysis
- [capability] Units can be ranked by relevance using BM25 algorithm
- [insight] One well-structured note with 10 observations beats 10 separate notes for search quality
- [architecture] Separation of concerns enables multiple query patterns on same content

## Database Schema Details

- [schema] Entity table uses permalink as unique identifier per project
- [schema] Observation table foreign key to entity_id creates parent-child relationship
- [schema] Relation table supports forward references with nullable to_id
- [checksum] Entity checksum enables fast change detection without reading file content
- [normalization] Database normalization reduces redundancy and improves query efficiency
- [indexing] Each table indexed separately for optimal search performance

## Relations

- related-to [[Basic Memory Technical Architecture - Deep Dive for JavaScript Rebuild]]
- related-to [[How Basic Memory Guides Intelligent Note Creation and Search]]
- implements [[Knowledge Graphs]]
- uses [[SQLite]]
- uses [[BM25 Algorithm]]
