---
name: memory-research
description: "Research an external subject using web search, synthesize findings into a structured Basic Memory entity. Use when asked to research a company, person, technology, or topic — or when a bare name or URL is provided that implies a research request."
---

# Memory Research

Research an external subject, synthesize what you find, and create a structured Basic Memory entity — with the user's approval.

## When to Use

**Explicit triggers:**
- "Research [subject]"
- "Look up [subject]"
- "What do you know about [subject]?"
- "Evaluate [subject]"

**Implicit triggers (also activate this skill):**
- A bare name: "Terraform"
- A URL: "https://example.com"
- A name with context: "Acme Corp — saw them at the conference"

## Workflow

### Step 1: Web Research

Search for current information across multiple sources. Aim for 3-5 searches to build a well-rounded picture:

```
[subject name] site
[subject name] overview
[subject name] news [current year]
[subject name] [relevant domain keywords]
```

**What to gather by entity type:**

| Entity Type | Key Information |
|-------------|----------------|
| **Organization** | What they do, products/services, stage (startup/growth/public), funding, leadership, headquarters, employee count, notable partnerships or contracts |
| **Person** | Current role, organization, background, expertise, notable work, public presence |
| **Technology** | What it does, who maintains it, maturity, ecosystem, alternatives, adoption |
| **Topic/Domain** | Definition, current state, key players, trends, relevance to user's context |

### Step 2: Check Existing Knowledge

Before proposing a new entity, search Basic Memory:

```python
search_notes(query="Acme Corp")
search_notes(query="acme")
```

Try name variations — full name, abbreviation, acronym, domain name.

If the entity already exists:
- Report what you found in Basic Memory alongside your web research
- Offer to update the existing note with new information
- Use `edit_note` to append new observations or update outdated ones

If the entity doesn't exist, proceed to evaluation.

### Step 3: Evaluate and Summarize

Present your findings in a structured summary. Include all relevant information organized by section:

```markdown
## [Subject Name]

**Type:** [Organization / Person / Technology / Topic]

**Summary:** [2-4 sentences: what this is, why it matters, key distinguishing facts]

**Key Details:**
- [Organized by what's relevant for the entity type]
- [Stage, funding, leadership for orgs]
- [Role, expertise, affiliations for people]
- [Maturity, ecosystem, alternatives for tech]

**Relevance:** [Why this matters to the user — connection to their work, domain, or interests.
If no obvious connection: "No specific connection identified."]

**Sources:**
- [URLs of key sources consulted]
```

### Evaluation Guidelines

**Use hedging language.** Web research is a snapshot, not ground truth:
- "Appears to be", "Based on public information", "Estimated"
- "As of [date]", "According to [source]"
- Never state funding amounts, employee counts, or revenue as exact unless citing a primary source

**Don't fabricate.** If information isn't available, say so:
- "Leadership information not publicly available"
- "Funding details not disclosed"

**Let the user define relevance.** Don't impose a fixed evaluation framework. Instead, highlight facts and let the user draw conclusions. If the user has a specific evaluation rubric (strategic fit, buy/partner/compete, etc.), they'll tell you — apply it when asked.

### Step 4: Propose Entity Creation

After presenting the summary, ask for approval:

```
Create Basic Memory entity for [Subject]?
  Location: [suggested-folder]/[entity-name].md
  Type: [entity type]

  [yes / no / modify]
```

If the user provided context with their request ("saw them at the conference"), include that context in the proposed entity.

### Step 5: Create the Entity

After approval, create a structured note. Adapt the template to the entity type:

#### Organization

```python
write_note(
  title="Acme Corp",
  directory="organizations",
  note_type="organization",
  tags=["organization", "relevant-tags"],
  content="""# Acme Corp

## Overview
[2-3 sentence description from research]

## Products & Services
- [Key offerings discovered in research]

## Background
**Stage:** [Startup / Growth / Public]
**Headquarters:** [Location]
**Employees:** [Estimate, hedged]
**Leadership:** [Key people if found]
**Founded:** [Year if found]

## Observations
- [relevance] Why this entity matters in user's context
- [source] Researched on YYYY-MM-DD
- [additional observations from research findings]

## Relations
- [Link to related entities already in the knowledge graph]"""
)
```

#### Person

```python
write_note(
  title="Jane Smith",
  directory="people",
  note_type="person",
  tags=["person", "relevant-tags"],
  content="""# Jane Smith

## Overview
[Current role and affiliation. Brief background.]

## Background
**Role:** [Title at Organization]
**Expertise:** [Key domains]
**Notable:** [Publications, talks, projects if found]

## Observations
- [role] Title at Organization
- [expertise] Key technical or domain expertise
- [source] Researched on YYYY-MM-DD

## Relations
- works_at [[Organization]]"""
)
```

#### Technology

```python
write_note(
  title="Technology Name",
  directory="concepts",
  note_type="concept",
  tags=["concept", "technology", "relevant-tags"],
  content="""# Technology Name

## Overview
[What it is and what problem it solves]

## Key Details
**Maintained by:** [Organization or community]
**Maturity:** [Experimental / Stable / Mature]
**License:** [If applicable]
**Alternatives:** [Comparable tools or approaches]

## Observations
- [definition] What this technology does in one sentence
- [maturity] Current state and adoption level
- [source] Researched on YYYY-MM-DD

## Relations
- [Link to related concepts, tools, or projects in the knowledge graph]"""
)
```

Adapt these templates freely. The key elements are: note_type/tags parameters, an overview, structured details, observations with categories, and relations.

### Step 6: Store Source Context

If the user provided context with their request, capture it in the entity:

```python
# User said: "Acme Corp — saw their demo at the conference last week"
edit_note(
  identifier="Acme Corp",
  operation="append",
  section="Observations",
  content="- [context] Saw their demo at conference, week of 2026-02-17"
)
```

This context is often the most valuable part — it's the user's relationship to the entity, which web research can't provide.

## Guidelines

- **Always web search.** Don't rely on training data alone. Research should reflect current, verifiable information.
- **Search Basic Memory first.** Check for existing entities before creating new ones. Update rather than duplicate.
- **Hedge uncertain information.** Use qualifiers for estimates, unverified claims, and inferred details.
- **Store source URLs.** Include the URLs you consulted, either in observations or a Sources section. This enables the user to verify and dig deeper.
- **Get approval before creating.** Present your findings and let the user decide whether to create the entity and what to include.
- **Capture user context.** If the user told you *why* they're researching (met at a conference, evaluating as a vendor, etc.), that context belongs in the entity.
- **Don't over-research.** 3-5 web searches is usually enough. The goal is a useful knowledge graph entry, not an exhaustive report.
- **Link to existing knowledge.** Relate the new entity to things already in the knowledge graph. Connections compound value.
