---
description: Research a topic and save a structured report to Basic Memory
argument-hint: <topic> [folder]
allowed-tools: mcp__basic-memory__write_note, mcp__basic-memory__search_notes, mcp__basic-memory__read_note, mcp__basic-memory__build_context, WebSearch, WebFetch, Grep, Glob, Read
---

# Research

Research a topic thoroughly and produce a structured report saved to Basic Memory.

## Arguments

- `$1` - Topic to research (required)
- `$2` - Folder to save report (optional, default: "research")

## Your Task

Conduct thorough research on: **$ARGUMENTS**

### 1. Check Existing Knowledge

First, see what we already know:
```python
mcp__basic-memory__search_notes(query="$1", project="main")
```

Read any relevant existing notes to avoid duplicating research.

### 2. Gather Information

Depending on the topic, use appropriate tools:

**For codebase topics:**
- Search code with Grep/Glob
- Read relevant files
- Check tests for examples

**For external topics:**
- Use WebSearch for current information
- Fetch documentation with WebFetch
- Look for official sources

**For Basic Memory context:**
- Build context from related notes
- Check for prior decisions or research

### 3. Analyze Findings

Synthesize what you learned:
- Identify key concepts
- Note patterns and trade-offs
- Form recommendations if applicable
- Flag uncertainties

### 4. Produce Report

Create a structured report with this format:

```markdown
---
title: "Research: [Topic]"
type: research
tags:
- research
- [relevant-tags]
---

# Research: [Topic]

## Summary

[2-3 sentence executive summary]

## Research Question

[What we investigated and why]

## Key Findings

### [Finding 1]
[Details and evidence]

### [Finding 2]
[Details and evidence]

### [Finding 3]
[Details and evidence]

## Analysis

[Synthesis, patterns, trade-offs, recommendations]

## Open Questions

- [Areas needing more investigation]

## Sources

- [Links to sources]
- [[Related Notes]] from Basic Memory

## Observations

- [finding] Key insight #research
- [recommendation] Suggested approach based on research

## Relations

- researches [[Topic]]
- relates-to [[Related Concepts]]
```

### 5. Save Report

```python
mcp__basic-memory__write_note(
    title="Research: $1",
    content="[report content]",
    folder="$2" or "research",
    tags=["research", ...],
    project="main"
)
```

### 6. Present Summary

After saving, present:
- Key findings summary
- Main recommendation (if applicable)
- Where the report was saved
- Offer to dive deeper into any aspect

## Examples

```
/research MCP protocol
/research "database migration patterns"
/research "authentication options" decisions
/research "React vs Vue" architecture
```
