---
name: research
description: Research a topic thoroughly and produce a structured report saved to Basic Memory - investigate concepts, gather context, and document findings
---

# Research

This skill helps conduct thorough research on a topic and produces a structured report that gets saved to Basic Memory for future reference.

## When to Use

Use this skill when:
- User asks to research or investigate something
- User wants to understand a concept, technology, or approach
- User needs context gathered before making a decision
- User asks "what is...", "how does... work", "explore...", "investigate..."
- User wants findings documented for later
- Phrases like "research this", "look into", "find out about", "explore options for"

## Research Process

### 1. Understand the Research Question

Clarify what specifically to investigate:
- What is the core question or topic?
- What scope - broad overview or deep dive?
- Any specific aspects to focus on?
- What will the research inform (a decision, implementation, understanding)?

### 2. Gather Information

Use available tools to collect information:

**For codebase research:**
- Search the codebase for relevant code
- Read documentation and comments
- Trace how things connect
- Look at tests for usage examples

**For concept research:**
- Use web search for current information
- Fetch documentation from official sources
- Look for examples and best practices
- Compare alternatives if relevant

**For Basic Memory context:**
```python
# Check what we already know
mcp__basic-memory__search_notes(
    query="topic keywords",
    project="main"
)

# Build context from related notes
mcp__basic-memory__build_context(
    url="memory://related-topic",
    depth=2,
    project="main"
)
```

### 3. Analyze and Synthesize

Organize findings into coherent insights:
- Identify key concepts and how they relate
- Note patterns, trade-offs, and considerations
- Highlight what's most relevant to the user's needs
- Flag uncertainties or areas needing more investigation

### 4. Produce the Report

Create a structured research report:

```markdown
---
title: "Research: [Topic]"
type: research
tags:
- research
- [topic-tags]
---

# Research: [Topic]

## Summary

[2-3 sentence executive summary of findings]

## Research Question

[What we set out to understand]

## Key Findings

### [Finding 1]
[Details, evidence, implications]

### [Finding 2]
[Details, evidence, implications]

### [Finding 3]
[Details, evidence, implications]

## Analysis

[Synthesis of findings - patterns, trade-offs, recommendations]

## Open Questions

- [Things that need more investigation]
- [Uncertainties or assumptions]

## Sources

- [Where information came from]
- [[Related Note]] - relevant prior knowledge

## Observations

- [finding] Key insight discovered #research
- [pattern] Pattern identified during research
- [recommendation] Suggested approach based on findings

## Relations

- researches [[Topic]]
- informs [[Decision or Implementation]]
- relates-to [[Related Concepts]]
```

### 5. Save to Basic Memory

```python
mcp__basic-memory__write_note(
    title="Research: [Topic]",
    content="[Full report content]",
    folder="research",
    tags=["research", "topic-tags"],
    project="main"
)
```

## Report Styles

Adjust based on the research type:

### Quick Investigation
- Focused summary
- 2-3 key findings
- Direct recommendation
- Saved to `research/` folder

### Deep Dive
- Comprehensive analysis
- Multiple sections
- Detailed evidence
- Comparison of options
- Saved to `research/` folder

### Decision Support
- Options evaluated
- Pros/cons for each
- Clear recommendation with rationale
- Saved to `decisions/` or `research/` folder

### Technical Exploration
- How it works
- Architecture/design
- Code examples
- Integration considerations
- Saved to `research/` folder

## Best Practices

1. **Start with what we know** - Check Basic Memory for existing context
2. **Be thorough but focused** - Cover the topic well without tangents
3. **Cite sources** - Link to where information came from
4. **Be honest about uncertainty** - Flag what's unclear or needs verification
5. **Make it actionable** - Include recommendations when appropriate
6. **Link to related knowledge** - Connect to existing notes
7. **Save for future reference** - Always save the report to Basic Memory

## Example Conversations

**User:** "Research how other projects handle database migrations"

**Claude:**
1. Searches codebase for migration patterns
2. Checks Basic Memory for related decisions
3. Looks up best practices online
4. Produces report comparing approaches
5. Saves to `research/Database Migration Approaches.md`
6. Presents summary with recommendation

**User:** "Investigate the MCP protocol"

**Claude:**
1. Fetches MCP documentation
2. Searches for examples in codebase
3. Checks Basic Memory for prior context
4. Produces comprehensive report on MCP
5. Saves to `research/MCP Protocol Overview.md`
6. Presents key concepts and how to use them

**User:** "Look into authentication options for the API"

**Claude:**
1. Researches common auth patterns (JWT, OAuth, API keys)
2. Checks existing codebase auth implementation
3. Evaluates trade-offs for the use case
4. Produces decision-support report
5. Saves to `research/API Authentication Options.md`
6. Recommends approach with rationale
