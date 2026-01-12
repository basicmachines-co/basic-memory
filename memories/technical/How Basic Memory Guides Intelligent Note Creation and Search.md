---
title: How Basic Memory Guides Intelligent Note Creation and Search
type: note
permalink: technical/how-basic-memory-guides-intelligent-note-creation-and-search
tags:
- intelligence,search,bm25,prompts,ai-guidance
---

A deep dive into how Basic Memory guides LLMs to create well-structured notes and how intelligent search works.

## The Intelligence Layer: Multi-Level Guidance System

Basic Memory doesn't just store notes - it **teaches AI how to create semantic knowledge** through a multi-layered guidance system:

```
Layer 1: AI Assistant Guide (412 lines of instruction)
    ↓
Layer 2: MCP Tool Docstrings (teach by example)
    ↓
Layer 3: Structured Feedback (confirms learning)
    ↓
Layer 4: Intelligent Search (finds semantically)
```

## Layer 1: The AI Assistant Guide

### Location
- [file] src/basic_memory/mcp/resources/ai_assistant_guide.md
- [purpose] Primary instruction document for all LLMs using Basic Memory
- [size] 412 lines of structured teaching

### Core Teaching: Semantic Markdown Format

The guide explicitly teaches three patterns:

#### Pattern 1: Observations (Atomic Facts)

```markdown
Format: - [category] content #tag1 #tag2 (optional context)

Examples from guide:
- [technique] Blooming coffee grounds for 30 seconds improves extraction #brewing
- [preference] Water temperature between 195-205°F works best #temperature
- [equipment] Gooseneck kettle provides better control #tools
```

**Taught Categories:**
- [instruction] Common: idea, decision, question, fact, requirement, technique, recipe, preference
- [instruction] Technical: concept, pattern, gotcha, solution, problem, bug, feature
- [instruction] Project: requirement, constraint, assumption, decision, tradeoff

#### Pattern 2: Relations (Knowledge Graph Edges)

```markdown
Format: - relation_type [[Target Entity]] (optional context)

Examples from guide:
- pairs_with [[Light Roast Beans]]
- contrasts_with [[French Press Method]]
- requires [[Proper Grinding Technique]]
```

**Taught Relation Types:**
- [instruction] Logical: relates_to, implements, requires, extends, part_of
- [instruction] Temporal: follows, precedes, originated_from
- [instruction] Comparison: pairs_with, contrasts_with
- [instruction] Creative: inspired_by, alternative_to

#### Pattern 3: Forward References

- [teaching] "Feel free to reference entities that don't exist yet"
- [teaching] "Relations will automatically resolve when target entities are created"
- [benefit] Enables organic knowledge growth without planning

### The Knowledge Graph Priority Teaching

**Explicit instruction (Lines 17-32):**

> "Basic Memory's value comes from connections between notes, not just the notes themselves."

**Six commandments:**
1. [principle] Increase Semantic Density: Add multiple observations and relations
2. [principle] Use Accurate References: Match exact titles when possible
3. [principle] Create Forward References: Don't wait for entities to exist
4. [principle] Create Bidirectional Links: Connect from both directions
5. [principle] Use Meaningful Categories: Choose categories that convey semantics
6. [principle] Choose Precise Relations: Use specific relation types

### Concrete Examples Throughout

- [example] Coffee brewing methods (shows technique, preference, equipment categories)
- [example] Software design decisions (shows decision, reason, tradeoff)
- [example] Research notes (shows finding, question, hypothesis)
- [example] Recipe documentation (shows ingredient, step, variation)

## Layer 2: MCP Tool Docstrings (Teaching by Example)

### write_note Tool Guidance

**The tool docstring LLMs see:**

```python
"""Write a markdown note to the knowledge base.

The content can include semantic observations and relations using markdown syntax.

Observations format:
    - [category] Observation text #tag1 #tag2 (optional context)
    
    Examples:
    - [design] Files are the source of truth #architecture (All state from files)
    - [tech] Using SQLite for storage #implementation
    - [note] Need to add error handling #todo

Relations format:
    - Explicit: - relation_type [[Entity]] (optional context)
    - Inline: Any [[Entity]] reference creates a relation
    
    Examples:
    - depends_on [[Content Parser]] (Need for semantic extraction)
    - implements [[Search Spec]] (Initial implementation)
    - This feature extends [[Base Design]] and uses [[Core Utils]]

Parameters:
    title: str - Note title
    content: str - Note content with observations/relations
    folder: str - Relative folder path
    tags: List[str] - Optional metadata tags
    entity_type: str - Default "note"
"""
```

- [pattern] Shows exact syntax in every tool call
- [pattern] Provides multiple examples per concept
- [pattern] Demonstrates both explicit and inline relations

### search_notes Tool Guidance

**40+ search examples in the docstring:**

```python
"""Search across all content in the knowledge base.

## Search Syntax Examples

### Basic Searches
- search_notes("keyword") - Any content containing "keyword"
- search_notes("exact phrase") - Exact phrase match

### Boolean Searches
- search_notes("term1 AND term2") - Both terms required
- search_notes("term1 OR term2") - Either term present
- search_notes("(project OR planning) AND notes") - Grouped logic

### Type Filtering
- search_notes("query", types=["entity"]) - Only entities
- search_notes("query", types=["observation"]) - Only observations

### Time Filtering
- search_notes("query", after_date="1 week") - Recent content
- search_notes("query", after_date="2025-01-01") - Since date
"""
```

- [teaching] Every possible search pattern with examples
- [teaching] Error messages that suggest corrections
- [teaching] Filters and parameters explained with use cases

## Layer 3: Structured Feedback (Confirming Learning)

### write_note Response Format

When an LLM creates a note, it receives **immediate structured feedback**:

```markdown
# Created note
file_path: guides/react-hooks.md
permalink: guides/react-hooks
checksum: a3f4c2b1

## Observations
- technique: 1
- best-practice: 1
- tip: 1

## Relations
- Resolved: 2
- Unresolved: 1

Note: Unresolved relations point to entities that don't exist yet.
They will be automatically resolved when target entities are created.

## Tags
- react, hooks, frontend
```

**What this teaches:**
- [feedback] Categories were recognized (technique, best-practice, tip)
- [feedback] Counts confirm how many observations extracted
- [feedback] Relation resolution status (2 targets exist, 1 forward reference)
- [feedback] Confirmation that forward references are okay
- [feedback] Tags parsed correctly

### Error Feedback as Teaching

When search syntax is wrong:

```markdown
# Search Failed - Invalid Syntax

Your query: testing*special(chars

Common syntax issues:
1. Special characters like +, *, " have special meaning
2. Unmatched quotes
3. Invalid operators

Examples of valid searches:
- Simple text: project planning
- Boolean AND: project AND planning
- Exact phrases: "weekly standup meeting"
- Try your search again with: testing special chars
```

- [teaching] Explains what went wrong
- [teaching] Shows correct alternatives
- [teaching] Suggests corrected query

## Layer 4: Intelligent Search (BM25 + Multi-Index)

### The Multi-Index Strategy

**One note creates multiple searchable entries:**

```markdown
# React Hooks Guide

## Observations
- [technique] useState adds state to functional components #hooks
- [tip] useCallback prevents function recreation #performance

## Relations
- implements [[React API]]
- part_of [[Frontend Development]]
```

**Creates 5 searchable index entries:**

1. **Entity entry:**
   - [indexed] title: "React Hooks Guide"
   - [indexed] content_stems: full text + title variants + permalink segments
   - [indexed] permalink: "guides/react-hooks"
   - [indexed] type: "entity"

2. **Observation entry 1:**
   - [indexed] category: "technique"
   - [indexed] content: "useState adds state to functional components"
   - [indexed] tags: "hooks"
   - [indexed] type: "observation"
   - [indexed] permalink: "guides/react-hooks/observations/technique/use-state-adds-state"

3. **Observation entry 2:**
   - [indexed] category: "tip"
   - [indexed] content: "useCallback prevents function recreation"
   - [indexed] tags: "performance"
   - [indexed] type: "observation"
   - [indexed] permalink: "guides/react-hooks/observations/tip/use-callback-prevents"

4. **Relation entry 1:**
   - [indexed] relation_type: "implements"
   - [indexed] target: "React API"
   - [indexed] from_id: entity.id
   - [indexed] type: "relation"

5. **Relation entry 2:**
   - [indexed] relation_type: "part_of"
   - [indexed] target: "Frontend Development"
   - [indexed] from_id: entity.id
   - [indexed] type: "relation"

### BM25 Ranking Algorithm

**What is BM25?**
- [algorithm] Best Match 25 - probabilistic relevance ranking
- [algorithm] Considers term frequency (TF) and inverse document frequency (IDF)
- [algorithm] Built into SQLite FTS5 as `bm25(search_index)` function
- [algorithm] Lower scores = better matches (FTS5 convention)

**How BM25 scores a query:**

```
Query: "React hooks performance"

For each document:
  score = sum of (term_weight × document_boost)

Term weight formula:
  IDF × (TF × (k1 + 1)) / (TF + k1 × (1 - b + b × (doc_length / avg_doc_length)))

Where:
  - IDF = log((N - df + 0.5) / (df + 0.5))
  - TF = term frequency in document
  - k1 = 1.2 (term frequency saturation parameter)
  - b = 0.75 (length normalization parameter)
  - N = total documents
  - df = documents containing term
  - doc_length = current document length
  - avg_doc_length = average across all documents
```

**Example scoring:**

```
Document A: "React hooks enable state in functional components"
  - "React": appears 1 time, common word (lower IDF)
  - "hooks": appears 1 time, moderately common (medium IDF)
  - "performance": appears 0 times
  Score: -2.3

Document B: "useCallback hook improves React performance by preventing rerenders"
  - "React": appears 1 time
  - "hooks": appears 0 times (but "hook" matches via stemming)
  - "performance": appears 1 time, less common (higher IDF)
  Score: -3.8 (better match!)

Document C: "React hooks performance optimization guide"
  - "React": appears 1 time
  - "hooks": appears 1 time
  - "performance": appears 1 time
  Score: -5.1 (best match!)
```

- [insight] All three terms present = highest score
- [insight] Rare terms (like "performance") weight more than common ("React")
- [insight] Shorter documents with same terms score higher (length normalization)

### Text Variant Generation for Fuzzy Matching

**The secret sauce: content_stems includes variants**

```javascript
// Original: "React Hooks Guide"

Variants generated:
- Original: "React Hooks Guide"
- Lowercase: "react hooks guide"
- Path segments (if from file path): ["react", "hooks", "guide"]
- Word boundaries: ["react", "hooks", "guide"]
- Trigrams: ["rea", "eac", "act", "hoo", "ook", "oks", "gui", "uid", "ide"]
```

**Why this matters:**

```
Query: "reakt hooks" (typo!)
  - Won't match "React" exactly
  - WILL match trigram "eak" (from "react")
  - User still finds the note!

Query: "hook" (singular)
  - Matches word boundary "hooks" via FTS5 stemming
  - User finds notes about "hooks"

Query: "guides/react"
  - Matches path segment "react"
  - Matches category "guides"
  - User finds by folder structure
```

### Search Query Preparation (The Safety Layer)

**FTS5 has special syntax that can break queries:**

```
Special characters: " * ( ) [ ] { } + ! @
Boolean operators: AND OR NOT
Wildcards: *
Phrases: "exact match"
```

**Query preparation handles this:**

```python
# Input: 'testing*special(chars'
# Problem: Unmatched parens, stray *
# Prepared: '"testing*special(chars"*'
# Result: Safely searches for literal string

# Input: 'React AND hooks'
# Problem: AND is boolean operator
# Prepared: 'React AND hooks' (preserved!)
# Result: Both terms required

# Input: 'React hooks'
# Problem: Multi-word, no operator specified
# Prepared: 'React* AND hooks*'
# Result: Both terms required with prefix matching
```

### Search Execution Flow

```
User query: "React performance tips"
        ↓
Query preparation:
  - Detect: no boolean operators
  - Split: ["React", "performance", "tips"]
  - Add wildcards: ["React*", "performance*", "tips*"]
  - Join with AND: "React* AND performance* AND tips*"
        ↓
FTS5 search:
  SELECT *, bm25(search_index) as score
  FROM search_index
  WHERE content_stems MATCH 'React* AND performance* AND tips*'
     OR title MATCH 'React* AND performance* AND tips*'
  ORDER BY score ASC
        ↓
Results ranked by BM25:
  1. "React Performance Tips" (score: -6.2) ← all 3 terms in title
  2. "React Hooks Guide" (score: -3.1) ← 2 terms in observations
  3. "Frontend Development" (score: -1.8) ← 1 term in content
        ↓
Return top N results with context
```

## Concrete Example: End-to-End Intelligence

### Step 1: LLM Reads AI Guide

LLM learns:
- [learning] Use [category] for observations
- [learning] Use relation_type [[Target]] for relations
- [learning] Connections matter more than isolated notes
- [learning] Forward references are encouraged

### Step 2: LLM Creates Note

```markdown
# React Hooks Best Practices

## Observations
- [rule] Always call hooks at the top level, never in conditions
- [rule] Only call hooks from React functions, not regular JS
- [pattern] Custom hooks should start with "use" prefix
- [gotcha] Can't conditionally call useState - violates rules of hooks
- [tip] useCallback and useMemo help with performance optimization

## Relations
- part_of [[React]]
- extends [[Functional Components]]
- related_to [[Performance Optimization]]
- contrasts_with [[Class Components]]
```

### Step 3: System Parses and Indexes

**Observations extracted:**
```
[0] category="rule", content="Always call hooks at top level..."
[1] category="rule", content="Only call hooks from React functions..."
[2] category="pattern", content="Custom hooks should start with 'use'..."
[3] category="gotcha", content="Can't conditionally call useState..."
[4] category="tip", content="useCallback and useMemo help with performance..."
```

**Relations extracted:**
```
[0] type="part_of", target="React", resolved=true (exists)
[1] type="extends", target="Functional Components", resolved=true
[2] type="related_to", target="Performance Optimization", resolved=false (forward ref)
[3] type="contrasts_with", target="Class Components", resolved=true
```

**Search index created:**
- 1 entity entry (title + content + variants)
- 5 observation entries (each searchable by category + content)
- 4 relation entries (each searchable by type + target)
- Total: 10 searchable entries from one note!

### Step 4: LLM Receives Feedback

```markdown
# Created note
file_path: guides/react-hooks-best-practices.md
permalink: guides/react-hooks-best-practices

## Observations
- gotcha: 1
- pattern: 1
- rule: 2
- tip: 1

## Relations
- Resolved: 3
- Unresolved: 1

Note: Unresolved relations point to entities that don't exist yet.
```

**LLM learns from feedback:**
- [reinforcement] Categories recognized correctly (rule, pattern, gotcha, tip)
- [reinforcement] Observation counts match expectations (2 rules, 1 pattern, etc.)
- [reinforcement] Most relations resolved (3 of 4)
- [reinforcement] Forward reference to "Performance Optimization" is okay

### Step 5: Future Search Works Intelligently

**Query: "hooks rules"**
- Matches observations with category="rule"
- BM25 ranks by relevance
- Returns: React Hooks Best Practices (2 rule observations match)

**Query: "performance optimization"**
- Matches relation target (even though unresolved!)
- Finds: React Hooks Best Practices
- Result: "This note relates to [[Performance Optimization]]"

**Query: "use prefix"**
- Matches observation content via trigrams
- Finds: React Hooks Best Practices
- Highlights: "Custom hooks should start with 'use' prefix"

**Query: "contrasts_with AND class"**
- Boolean search on relation type + target
- Finds: React Hooks Best Practices
- Result: "Contrasts with [[Class Components]]"

## Key Intelligence Mechanisms

### 1. Teaching Through Examples

- [pattern] Every tool shows 5-10 examples of correct usage
- [pattern] AI guide has 20+ real-world examples
- [pattern] Error messages suggest corrections with examples

### 2. Immediate Reinforcement

- [pattern] Every write returns observation/relation counts
- [pattern] Resolution status confirms understanding
- [pattern] Structured feedback confirms parsing

### 3. Multi-Index Coverage

- [pattern] One note → 10+ searchable entries
- [pattern] Search by title, content, category, relation, tag
- [pattern] All paths lead to relevant notes

### 4. Fuzzy Matching

- [pattern] Title variants catch alternative phrasings
- [pattern] Trigrams catch typos
- [pattern] Stemming catches singular/plural differences

### 5. Semantic Understanding

- [pattern] Categories add meaning (rule vs tip vs gotcha)
- [pattern] Relation types convey semantics (part_of vs contrasts_with)
- [pattern] Tags provide metadata dimensions

### 6. BM25 Relevance

- [pattern] Rare terms weighted higher than common
- [pattern] Multiple term matches rank higher
- [pattern] Shorter documents with same terms rank higher

## Why This Architecture is Powerful

### For LLMs

- [benefit] Clear patterns to follow (category syntax, relation syntax)
- [benefit] Immediate feedback confirms correct structure
- [benefit] Examples in every interaction
- [benefit] Forward references reduce friction

### For Users

- [benefit] Search finds content even with typos
- [benefit] Multiple access paths to same information
- [benefit] Relevant results ranked by BM25
- [benefit] Semantic categories improve precision

### For Knowledge Growth

- [benefit] Forward references enable organic growth
- [benefit] Unresolved relations resolve automatically
- [benefit] Rich interconnections emerge naturally
- [benefit] Knowledge density increases over time

## Relations

- related_to [[Basic Memory]]
- related_to [[BM25 Algorithm]]
- related_to [[Full-Text Search]]
- related_to [[Knowledge Graph]]
- part_of [[Basic Memory Technical Architecture - Deep Dive for JavaScript Rebuild]]