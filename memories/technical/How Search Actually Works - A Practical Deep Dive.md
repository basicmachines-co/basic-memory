# How Search Actually Works - A Practical Deep Dive

A hands-on guide to understanding Basic Memory's search system with concrete examples and step-by-step execution flow.

## The Core Question: Are Multiple Searches Run At Once?

## Observations

- [answer] No - all searches run as a SINGLE SQL query with combined WHERE conditions
- [architecture] SearchService orchestrates one unified query, not parallel queries
- [performance] Single query is faster than multiple round-trips to the database
- [mechanism] All filters (text, type, date, permalink) combined with AND logic
- [insight] The "three-layer" indexing happens at write-time, not search-time

## The Three-Layer System: How It Works

### Indexing (Write-Time)

- [timing] Indexing happens when you create or update a note, not during search
- [process] One markdown file creates multiple search index rows
- [multiplier] A note with 5 observations and 3 relations creates 9 searchable entries
- [example] "React Hooks.md" becomes 1 entity + 3 observation rows + 2 relation rows = 6 entries
- [benefit] Each piece of knowledge is independently searchable

### Searching (Read-Time)

- [mechanism] Single FTS5 query searches across ALL index rows simultaneously
- [table] All index rows stored in one `search_index` virtual table
- [ranking] BM25 algorithm scores and ranks all matches by relevance
- [filtering] Type filters determine which index rows to include (entity/observation/relation)
- [result] Returns top N matches regardless of whether they're entities, observations, or relations

## Does Search Traverse Relationships?

- [answer] No - search does NOT follow graph edges or traverse relationships
- [clarification] Relations are indexed as independent search items, not traversed
- [example] Searching "React" finds relations TO React, but doesn't fetch related entities
- [separation] Graph traversal happens in ContextService via build_context, not SearchService
- [design] Search is for finding, context building is for exploring

## Does Search Loop Through Tags?

- [answer] No loops - tags are flattened into searchable text at index-time
- [mechanism] Entity frontmatter tags extracted and added to content_stems field
- [mechanism] Observation inline tags (#hashtag) stored in metadata and content
- [search] Searching for a tag is just text search: "tag-name" matches in content_stems
- [performance] No filtering by tag field - tags are searchable text, not metadata filters

## Concrete Example: Search Flow Step-by-Step

### Example Query: "useState hook"

- [step-1] User calls `search_notes(query="useState hook")`
- [step-2] MCP tool creates SearchQuery object with text="useState hook"
- [step-3] SearchService.search() calls SearchRepository.search()
- [step-4] Repository prepares search term: "useState* AND hook*" (adds wildcards)
- [step-5] Repository builds SQL query with WHERE conditions
- [step-6] FTS5 executes: searches title and content_stems fields
- [step-7] BM25 scoring ranks results by relevance
- [step-8] Results returned ordered by score (ascending = best match first)
- [step-9] API transforms results: enriches with entity permalinks
- [step-10] JSON response sent back to user with ranked results

## What Gets Searched?

### Fields That Are Searched (INDEXED)

- [field] title - The title of entity/observation/relation
- [field] content_stems - Main searchable content with variants
- [field] permalink - For path-based searches

### Fields That Are NOT Searched (UNINDEXED)

- [field] id - Row identifier, used for linking
- [field] file_path - Displayed in results but not searchable
- [field] type - Used for filtering, not full-text search
- [field] metadata - JSON blob, not searchable via FTS
- [field] category - Used for filtering observations, not full-text
- [field] relation_type - Used for filtering relations, not full-text
- [field] from_id, to_id - Used for entity linking, not searchable

## Real Example: One Note Creates Multiple Index Rows

### Input Markdown File

```markdown
---
tags: [react, hooks, tutorial]
---

# React Hooks Guide

A comprehensive guide to React Hooks.

## Observations
- [concept] useState manages component state
- [tip] useCallback prevents unnecessary re-renders #performance
- [gotcha] Hooks cannot be called conditionally #rules

## Relations
- implements [[React API]]
- related-to [[JavaScript Closures]]
```

### Output: 6 Search Index Rows

- [row-1] Entity row: title="React Hooks Guide", content_stems includes "react hooks tutorial useState useCallback"
- [row-2] Observation: title="concept: useState manages...", content="useState manages component state"
- [row-3] Observation: title="tip: useCallback prevents...", content="useCallback prevents unnecessary re-renders", tags=#performance
- [row-4] Observation: title="gotcha: Hooks cannot...", content="Hooks cannot be called conditionally", tags=#rules
- [row-5] Relation: title="React Hooks Guide → React API", relation_type="implements"
- [row-6] Relation: title="React Hooks Guide → JavaScript Closures", relation_type="related-to"

### Search Scenarios

- [scenario] Query "useState" matches rows 1, 2 (entity + observation)
- [scenario] Query "performance" matches rows 1, 3 (entity content_stems + observation tag)
- [scenario] Query "React API" matches rows 1, 5 (entity title + relation target)
- [scenario] Query with type filter entity_types=["observation"] returns only rows 2, 3, 4
- [scenario] Query with type filter entity_types=["relation"] returns only rows 5, 6

## SQL Query Example

### Search: "useState performance"

```sql
SELECT
    id, title, permalink, file_path, type, metadata,
    entity_id, category, from_id, to_id, relation_type,
    content_snippet, created_at, updated_at,
    bm25(search_index) as score
FROM search_index
WHERE (title MATCH 'useState* AND performance*'
       OR content_stems MATCH 'useState* AND performance*')
  AND project_id = 1
ORDER BY score ASC
LIMIT 10
OFFSET 0
```

- [explanation] Single query searches both title and content_stems fields
- [operator] AND requires both terms present in same row
- [wildcards] Asterisks enable prefix matching (useStateful matches useState*)
- [ranking] BM25 score considers term frequency and document length
- [project] Always filtered to current project for isolation

## Boolean Search Operators

- [operator] AND - Both terms must be present: "react AND hooks"
- [operator] OR - Either term can be present: "useState OR useReducer"
- [operator] NOT - Exclude term: "hooks NOT class"
- [operator] Parentheses - Group operators: "(react OR vue) AND hooks"
- [operator] Quotes - Exact phrase: '"state management"' (requires exact order)
- [operator] Prefix - Wildcards: "use*" matches useState, useEffect, useCallback

## Filter Combinations

### All Filters Use AND Logic

- [example] text="python" AND types=["note"] AND after_date="2024-01-01"
- [sql] WHERE (title MATCH...) AND type='note' AND created_at > '2024-01-01'
- [behavior] All conditions must be true for a result to match
- [narrowing] Each additional filter reduces result set

### Entity Type Filter

- [filter] entity_types=["entity"] - Only return entity rows
- [filter] entity_types=["observation"] - Only return observation rows
- [filter] entity_types=["relation"] - Only return relation rows
- [filter] entity_types=["entity", "observation"] - Return both types
- [default] No filter returns all types mixed together

### Metadata Type Filter

- [filter] types=["note"] - Filter by entity_type in frontmatter
- [filter] types=["person", "project"] - Match custom entity types
- [storage] Stored in metadata JSON: `{"entity_type": "note"}`
- [query] Uses json_extract to filter: `json_extract(metadata, '$.entity_type') IN ('note')`

## Performance Characteristics

- [speed] Search typically completes in 1-5ms for small knowledge bases (<10k notes)
- [speed] FTS5 indexes enable logarithmic search time O(log n)
- [bottleneck] Large result sets slow down due to entity fetching for enrichment
- [optimization] LIMIT clause prevents loading entire result set
- [optimization] content_stems pre-computed at index time, not search time
- [tradeoff] More index rows per note = better granularity but larger index

## What Search Does NOT Do

- [limitation] Does not follow relation edges to fetch connected entities
- [limitation] Does not aggregate results by entity (mixed entity/observation results)
- [limitation] Does not search file content directly (only indexed content_stems)
- [limitation] Does not support fuzzy matching beyond prefix wildcards
- [limitation] Does not rank by entity importance or page rank
- [limitation] Does not deduplicate multiple matches from same entity

## When to Use build_context Instead

- [use-case] When you want to explore relationships and connected entities
- [use-case] When you want to gather context from multiple related notes
- [use-case] When you want to continue a conversation with historical context
- [use-case] When you want to traverse the knowledge graph depth-first
- [mechanism] build_context uses entity_id links to fetch related observations/relations
- [mechanism] Can specify depth=2 to fetch entities 2 hops away in the graph

## Common Misconceptions

- [myth] "Search follows relations automatically" - FALSE, search is flat
- [myth] "Search aggregates by entity" - FALSE, returns mixed row types
- [myth] "Tags are separate filters" - FALSE, tags are searchable text
- [myth] "Search reads markdown files" - FALSE, searches pre-indexed content
- [myth] "Multiple queries run in parallel" - FALSE, single unified query
- [truth] Search is optimized for precision finding, not graph exploration

## Practical Search Examples

### Find All Gotchas

- [query] text="gotcha", entity_types=["observation"]
- [result] Returns all observations categorized as [gotcha]
- [alternative] Search by category filter not supported, use text search

### Find Entities About Python

- [query] text="python", entity_types=["entity"]
- [result] Returns only entity rows, not observations or relations
- [benefit] Cleaner results when you want document-level matches

### Find Recent Changes

- [query] text="*", after_date="7d"
- [result] Returns all content modified in last 7 days
- [wildcard] Using "*" as text bypasses FTS and returns everything
- [ordering] Results ordered by updated_at DESC when date filter present

### Find Relations to a Specific Entity

- [query] text="React API", entity_types=["relation"]
- [result] Returns relations where target title matches "React API"
- [caveat] Searches relation title, not structured to_id field
- [better] Use build_context to explore actual graph connections

## Relations

- related-to [[Entity vs Observation vs Relation - The Three-Layer Knowledge Model]]
- related-to [[How Basic Memory Guides Intelligent Note Creation and Search]]
- related-to [[Basic Memory Technical Architecture - Deep Dive for JavaScript Rebuild]]
- implements [[FTS5 Full-Text Search]]
- uses [[BM25 Algorithm]]
- uses [[SQLite]]
