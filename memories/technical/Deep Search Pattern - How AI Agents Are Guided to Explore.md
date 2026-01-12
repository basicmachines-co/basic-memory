# Deep Search Pattern - How AI Agents Are Guided to Explore

An analysis of how Basic Memory's AI Assistant Guide instructs agents to perform iterative, deep searches beyond simple queries.

## The Core Question: Is There a "Deep Search" Pattern?

## Observations

- [answer] Yes - the AI Assistant Guide contains explicit patterns for iterative exploration
- [location] Found in ai_assistant_guide.md lines 156-159 and 398-404
- [pattern] Search → Build Context → Read → Combine is the recommended workflow
- [insight] "Deep search" is not a single tool but a guided multi-step pattern
- [design] Agents are instructed to "build a complete picture before responding"

## The Guided Workflow Pattern

### Step 1: Initial Search

- [instruction] "Use search_notes() to find relevant notes" (line 157)
- [purpose] Locate potential starting points in the knowledge base
- [output] List of matching entities, observations, and relations
- [example] User asks "What were our decisions about auth?" → search_notes("auth decisions")

### Step 2: Build Context from Results

- [instruction] "Then build_context() to understand connections" (line 158)
- [purpose] Expand from search results to related knowledge
- [mechanism] Follows relation edges in the knowledge graph
- [example] Takes top search result permalink → build_context("memory://specs/auth", depth=2)
- [depth] Can traverse 1-3 hops to gather connected entities

### Step 3: Read Specific Content

- [instruction] "Then read_note() to access specific content" (line 149)
- [purpose] Get full markdown content of relevant entities
- [detail] Provides complete context including all observations and relations
- [example] read_note("specs/authentication-design")

### Step 4: Combine Information

- [instruction] "Combine information from multiple sources" (line 400)
- [instruction] "Build a complete picture before responding" (line 402)
- [pattern] Synthesize findings from search + context + reads
- [output] Comprehensive answer drawing from multiple connected notes

## Explicit Guidance in AI Assistant Guide

### "Finding Information" Pattern (Lines 151-159)

```markdown
Human: "What were our decisions about auth?"

You: Let me find that information for you.
[Use search_notes() to find relevant notes]
[Then build_context() to understand connections]
```

- [guidance] Two-step pattern: search THEN build context
- [implication] Single search is not enough - must follow up
- [workflow] Find → Explore → Synthesize

### "Navigate Knowledge Effectively" (Lines 397-402)

```markdown
4. **Navigate Knowledge Effectively**
   - Start with specific searches
   - Follow relation paths
   - Combine information from multiple sources
   - Verify information is current
   - Build a complete picture before responding
```

- [principle] Start specific (search) then expand (follow relations)
- [principle] Multiple sources required for complete picture
- [verification] Check recency with recent_activity()
- [synthesis] Don't respond until picture is complete

### "Creating Effective Relations" Workflow (Lines 263-326)

```python
# Search for existing entities to reference
search_results = await search_notes("travel")
existing_entities = [result.title for result in search_results.primary_results]

# Check if specific entities exist
packing_tips_exists = "Packing Tips" in existing_entities

# Check recently modified notes to reference them
recent = await recent_activity(timeframe="1 week")
recent_titles = [item.title for item in recent.primary_results]
```

- [pattern] Search to discover what exists
- [pattern] Check recent activity for additional context
- [pattern] Combine search + recent_activity for comprehensive view
- [workflow] Multiple queries to build complete understanding

## Real-World Deep Search Example

### User Question: "Tell me about our authentication approach"

#### Agent's Guided Workflow:

- [step-1] search_notes("authentication approach") → finds 3 entities, 5 observations, 2 relations
- [step-2] Take top result: "Authentication Design" (permalink: specs/auth-design)
- [step-3] build_context("memory://specs/auth-design", depth=2) → discovers:
  - Primary: Authentication Design entity
  - Related (depth 1): JWT Implementation, OAuth Provider, Session Management
  - Related (depth 2): Security Best Practices, User Table Schema
- [step-4] read_note("specs/auth-design") → get full markdown content
- [step-5] read_note("specs/jwt-implementation") → follow important relation
- [step-6] Synthesize: Combine auth design + JWT details + related security practices
- [step-7] Respond with comprehensive answer drawing from 5+ sources

### What Makes This "Deep Search"

- [characteristic] Multiple tool calls, not single query
- [characteristic] Follows graph edges via build_context
- [characteristic] Reads related content beyond initial matches
- [characteristic] Verifies with recent_activity when needed
- [characteristic] Synthesizes from multiple sources before responding

## Tools Used in Deep Search Pattern

### search_notes() - The Entry Point

- [role] Initial discovery of relevant content
- [strength] BM25 ranking finds best matches
- [limitation] Returns flat results, doesn't traverse graph
- [next-step] Feeds permalinks to build_context

### build_context() - The Graph Explorer

- [role] Traverse knowledge graph from search results
- [strength] Follows relation edges automatically
- [depth] Can go 1-3 hops deep (depth parameter)
- [output] Primary results + related results via relations
- [next-step] Identifies specific notes to read

### read_note() - The Detail Retriever

- [role] Get full content of specific entities
- [strength] Complete markdown including all observations/relations
- [usage] Called on multiple related entities
- [synthesis] Multiple reads combined for comprehensive understanding

### recent_activity() - The Verification Tool

- [role] Check what's been updated recently
- [usage] Verify information is current
- [pattern] Often combined with search for complete context
- [timeframe] "1 week", "1 month", "today" filters

## Is This Pattern Mandatory or Suggested?

- [nature] Suggested best practice, not enforced
- [location] Documented in "Best Practices" section (lines 370-411)
- [instruction] "Navigate Knowledge Effectively" implies multi-step pattern
- [flexibility] Agent can choose to search only for simple queries
- [guidance] Complex questions should trigger the full workflow

## Comparison: Simple Search vs Deep Search

### Simple Search (Single Tool)

```python
# User: "What's the status of feature X?"
results = await search_notes("feature X status")
# Return top result directly
```

- [use-case] Straightforward factual lookup
- [assumption] Answer contained in single entity
- [tools] 1 tool call
- [depth] Surface-level

### Deep Search (Multi-Tool Workflow)

```python
# User: "Tell me about our approach to feature X and how it relates to our architecture"
# Step 1: Search
results = await search_notes("feature X approach")

# Step 2: Build context
context = await build_context(f"memory://{results[0].permalink}", depth=2)

# Step 3: Read primary + related
feature_content = await read_note(results[0].permalink)
arch_content = await read_note(context.related[0].permalink)

# Step 4: Check recency
recent = await recent_activity(timeframe="1 week")

# Step 5: Synthesize comprehensive answer
```

- [use-case] Complex questions requiring synthesis
- [assumption] Answer requires understanding connections
- [tools] 4-5 tool calls
- [depth] Multi-hop graph traversal

## Why This Pattern Exists

- [reason] Knowledge graphs are more valuable than isolated notes
- [reason] Relations contain crucial context connections
- [reason] Single search results miss the "why" and "how it connects"
- [reason] Users expect comprehensive answers, not just keywords
- [principle] "A knowledge graph with 10 heavily connected notes is more valuable than 20 isolated notes" (line 31-32)

## Where This Pattern Is NOT Specified

- [missing] No explicit "always use 3+ tools for questions" rule
- [missing] No mandatory depth parameter guidance
- [missing] No decision tree for when to use deep vs simple search
- [flexibility] Agent judges complexity and chooses appropriate depth
- [inference] Agent must infer when comprehensive context is needed

## Agent Autonomy in Deep Search

- [freedom] Agent decides whether to go deep based on question complexity
- [guidance] Best practices suggest multi-step pattern for complex queries
- [instruction] "Build a complete picture before responding" (line 402)
- [instruction] "Combine information from multiple sources" (line 400)
- [implication] Agent should use judgment to determine search depth

## Example Prompts That Trigger Deep Search

- [trigger] "Tell me about X and how it relates to Y"
- [trigger] "What's our approach to X?"
- [trigger] "Explain X in context of Y"
- [trigger] "What were all our decisions about X?"
- [trigger] "Give me a comprehensive overview of X"
- [simple] "What is X?" → might use simple search only

## Enhancement Opportunity

- [observation] Current guidance is implicit and scattered
- [suggestion] Could add explicit "Deep Search Checklist" section
- [suggestion] Could specify when depth=1 vs depth=2 vs depth=3
- [suggestion] Could provide decision tree for search complexity
- [suggestion] Could add example showing before/after of deep search

## Relations

- related-to [[How Search Actually Works - A Practical Deep Dive]]
- related-to [[Entity vs Observation vs Relation - The Three-Layer Knowledge Model]]
- related-to [[How Basic Memory Guides Intelligent Note Creation and Search]]
- implements [[Knowledge Graph Traversal]]
- uses [[build_context Tool]]
- uses [[search_notes Tool]]
- uses [[read_note Tool]]
