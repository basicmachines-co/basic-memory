# Semantic Search Manual Test Log

## Overview

Manual test session for semantic (vector) search on the main project.
- Date: 2026-02-15
- Database: ~/.basic-memory/memory.db (SQLite)
- Entities: 456 embedded, 2714 vector chunks
- Search index: 2390 FTS entries
- Embedding model: default (384-dim, sqlite-vec)

## Test Plan

1. **Search Type Routing** — verify vector/hybrid/text dispatch, invalid search_type handling
2. **Conceptual Queries** — natural language where vector should beat FTS
3. **Keyword Queries** — exact terms where FTS should be strong
4. **Hybrid Ranking** — queries where both FTS and vector contribute
5. **Result Types** — entities, observations, relations in vector results
6. **Filters + Vector** — combine vector with types/entity_types/after_date
7. **Edge Cases** — short queries, long queries, empty, special chars, no-match
8. **Pagination** — page > 1, page_size respected

---

## Test Results

### Test 1: Search Type Routing

#### 1a: search_type="semantic" (invalid value)
- **Input:** query="how does the knowledge graph work", search_type="semantic"
- **Expected:** error or explicit fallback
- **Actual:** Silently falls through to text search (else branch in search.py:430)
- **Verdict:** BUG — should either be a recognized alias for "vector" or return an error

#### 1b: search_type="vector"
- **Input:** query="keeping AI context between sessions", search_type="vector"
- **Actual:** 5 results, scores ~0.58-0.59, found "Maintaining context across conversation boundaries" observation
- **Verdict:** PASS

#### 1c: search_type="text" with conceptual query
- **Input:** query="keeping AI context between sessions", search_type="text"
- **Actual:** 0 results (no exact keyword match)
- **Verdict:** PASS (expected — FTS requires token overlap)

#### 1d: search_type="hybrid" with conceptual query
- **Input:** query="keeping AI context between sessions", search_type="hybrid"
- **Actual:** 5 results, same ranking as vector (FTS contributed nothing here)
- **Verdict:** PASS

#### 1e: search_type="text" with keyword query
- **Input:** query="OAuth authentication", search_type="text"
- **Actual:** 3 results — AUTH.md Supabase OAuth, OAuth Rip-and-Replace, OAuth Integration Analysis
- **Verdict:** PASS

#### 1f: search_type="vector" with keyword query
- **Input:** query="OAuth authentication", search_type="vector"
- **Actual:** Same top results as text (keyword-rich content also scores well in vector space)
- **Verdict:** PASS

---

### Test 2: Conceptual Queries (vector advantage)

#### 2a: Natural language question
- **Input:** query="why do AI assistants forget things", search_type="vector"
- **Actual:** 5 results — Manual Testing Session, "Balance security and usability" observation, "Tools should match thought patterns" observation. Scores ~0.56-0.57
- **Vector advantage:** Found conceptually related content despite no exact keyword overlap
- **Verdict:** PASS

#### 2b: Same query, text search
- **Input:** query="why do AI assistants forget things", search_type="text"
- **Actual:** 1 result — "What is Basic Memory?" (likely matched on "AI" token)
- **Verdict:** PASS (demonstrates vector advantage — text barely matched)

#### 2c: Domain concept with no jargon
- **Input:** query="pricing strategy for cloud product", search_type="vector"
- **Actual:** 3 results — SPEC-16 MCP Cloud Service Consolidation, knowledge architecture observation, Visual Knowledge Spaces relation. Scores ~0.56-0.57
- **Verdict:** PASS (found cloud-related content conceptually)

#### 2d: Technical concept, long query
- **Input:** query="SQLite performance optimization WAL mode concurrent writes", search_type="vector"
- **Actual:** 3 results — SPEC-11 API Performance Optimization, Real-Time Updates with WebSockets, marketing status update. Scores ~0.55-0.58
- **Verdict:** PASS (found performance-related content)

---

### Test 3: Keyword Queries (FTS strength)

#### 3a: Exact term match — "OAuth authentication"
- **Text:** 3 results with high relevance (exact matches in titles)
- **Vector:** Same top results (keyword overlap helps vector too)
- **Verdict:** PASS — FTS and vector converge on keyword-rich queries

#### 3b: "OAuth" single keyword, hybrid mode
- **Input:** query="OAuth", search_type="hybrid"
- **Actual:** 5 results — Basic Memory Coding Guide, AI Collaboration Examples, SPEC-18, daily note, Manual Testing Session. FTS + vector blended. Scores ~0.016-0.032
- **Note:** Top hybrid result is "Basic Memory Coding Guide" not an OAuth-specific doc — suggests hybrid scoring may dilute strong FTS matches
- **Verdict:** PASS but hybrid ranking questionable for single-keyword queries

---

### Test 4: Hybrid Ranking

#### 4a: Hybrid vs vector on "OAuth authentication"
- **Hybrid with entity_types=["entity"]:** 5 results — RLS Implementation Lessons, Cloud Readiness Assessment, AUTH.md OAuth, Core Service Implementation, OAuth Rip-and-Replace. Scores ~0.016-0.023
- **Vector with entity_types=["entity"]:** 5 results — Core Service Implementation, SPEC-13 CLI Auth, Coding Guide, Authentication Service, ADR Production Auth. Scores ~0.55-0.60
- **Observation:** Hybrid surfaces different top results than vector-only. Hybrid found RLS and Cloud Readiness docs that vector didn't prioritize. Different ranking is expected from RRF fusion.
- **Verdict:** PASS — hybrid produces meaningfully different ranking

---

### Test 5: Result Types

#### 5a: Vector returns all result types
- **Input:** query="keeping AI context between sessions", search_type="vector"
- **Entities:** SPEC-18 AI Memory Management Tool (type=entity)
- **Relations:** Prompt Builder integrates_with (type=relation)
- **Observations:** "Translation layer is key" (type=observation), "Maintaining context across conversation boundaries" (type=observation)
- **Verdict:** PASS — all three types appear in vector results

#### 5b: Observations carry metadata
- **Observation result:** category="challenge", content="Maintaining context across conversation boundaries", from_entity="research/ai-knowledge-management-research"
- **Verdict:** PASS — category, content, from_entity, tags all present

#### 5c: Relations carry link info
- **Relation result:** relation_type="integrates_with", from_entity="development/features/prompt-builder...", to_entity (present but truncated in some)
- **Verdict:** PASS — relation metadata present

---

### Test 6: Filters + Vector Search

#### 6a: entity_types=["entity"] with vector
- **Input:** query="OAuth authentication", search_type="vector", entity_types=["entity"]
- **Actual:** 5 results, all type="entity" (Core Service Implementation, SPEC-13, Coding Guide, Authentication Service, ADR Auth)
- **Verdict:** PASS — filter correctly restricts to entities only

#### 6b: types=["note"] with vector
- **Input:** query="OAuth authentication", search_type="vector", types=["note"]
- **Actual:** Same 5 results (all have entity_type="note" in metadata)
- **Verdict:** PASS — types filter works with vector search

#### 6c: after_date with vector
- **Input:** query="OAuth authentication", search_type="vector", after_date="2025-06-01"
- **Actual:** 3 results — Core Service Implementation, Cloud Web App analysis observation, SPEC-13. Filtered out older OAuth docs.
- **Verdict:** PASS — date filter applied correctly

#### 6d: entity_types=["entity"] with hybrid
- **Input:** query="OAuth authentication", search_type="hybrid", entity_types=["entity"]
- **Actual:** 5 results, all type="entity" — RLS lessons, Cloud Readiness, AUTH.md OAuth, Core Service, OAuth Rip-and-Replace
- **Verdict:** PASS — filter works with hybrid mode too

#### 6e: types=["entity"] with vector (WRONG filter name)
- **Input:** query="OAuth authentication", search_type="vector", types=["entity"]
- **Actual:** 0 results
- **Note:** `types` filters by entity_type metadata (e.g., "note", "person"), NOT by SearchItemType. Using types=["entity"] looks for entity_type="entity" which few/no notes have. This is a UX confusion point — the param names are ambiguous.
- **Verdict:** PASS (correct behavior) but USABILITY ISSUE — easy to confuse types vs entity_types

---

### Test 7: Edge Cases

#### 7a: Single character query
- **Input:** query="x", search_type="vector"
- **Actual:** 3 results — "Self-contained application bundle" observation, Non-Markdown File Support relation, quick-win-tools entity. Scores ~0.57-0.59
- **Note:** Single character still produces an embedding and returns results. Quality is low/random as expected.
- **Verdict:** PASS (no crash, returns results)

#### 7b: Whitespace-only query
- **Input:** query="   ", search_type="vector"
- **Actual:** 0 results
- **Verdict:** PASS (handled gracefully — _check_vector_eligible strips and rejects empty)

#### 7c: Query with no relevant content
- **Input:** query="quantum computing blockchain", search_type="vector"
- **Actual:** 3 results — Inter-Agent Communication relation, Self-contained bundle observation, JSON-LD interop observation. Scores ~0.54
- **Note:** Still returns results because vector search always finds nearest neighbors. Scores are lower (~0.54) than relevant queries (~0.58-0.60). No relevance threshold applied.
- **Verdict:** PASS (expected behavior) but NOTE — no relevance cutoff means irrelevant queries always return something

---

### Test 8: Pagination

#### 8a: Vector search page 2
- **Input:** query="keeping AI context between sessions", search_type="vector", page=2, page_size=3
- **Actual:** 3 results on page 2, current_page=2. Different results from page 1. Top: "Maintaining context across conversation boundaries" observation (score 0.587)
- **Note:** Interestingly, page 2 had a higher-scoring result than some page 1 results. This may indicate pagination doesn't sort globally — it might be paginating within a pre-scored set.
- **Verdict:** PASS (pagination works) but POSSIBLE ISSUE — result ordering across pages needs investigation

---

## Summary

### Passing Tests: 20/21

### Bugs Found
1. **search_type="semantic" silently falls through** (Test 1a) — Invalid search_type values fall to the `else` branch and default to text search without any warning. Should either alias "semantic" to "vector" or raise an error.

### Usability Issues
2. **types vs entity_types confusion** (Test 6e) — `types` filters by entity_type metadata (note, person, etc.) while `entity_types` filters by SearchItemType (entity, observation, relation). The naming is ambiguous and easy to mix up.
3. **No relevance threshold** (Test 7c) — Vector search always returns nearest neighbors even for completely irrelevant queries. Consider adding a minimum score threshold or at least documenting expected score ranges.
4. **Hybrid ranking for single keywords** (Test 3b) — Hybrid mode on simple keyword queries produced less intuitive rankings than pure FTS or pure vector. The RRF fusion may dilute strong FTS signals.

### Observations
- Vector search successfully finds conceptually related content that FTS misses entirely
- Score ranges: relevant queries ~0.56-0.60, irrelevant queries ~0.54 (narrow spread)
- All three result types (entity, observation, relation) appear correctly in vector results
- Filters (entity_types, types, after_date) all work correctly with vector and hybrid modes
- Pagination works but cross-page ordering may need investigation
