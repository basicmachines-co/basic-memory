---
name: memory-ingest
description: "Process unstructured external input (meeting transcripts, conversation logs, pasted documents) into structured Basic Memory entities. Extracts entities, searches for existing matches, proposes new entities with approval, creates notes with observations and relations, and captures action items."
---

# Memory Ingest

Turn raw, unstructured input into structured Basic Memory entities. Meeting transcripts, conversation logs, pasted documents, email threads — anything with information worth preserving gets parsed, cross-referenced against existing knowledge, and written as proper notes.

## When to Use

- User pastes a meeting transcript or conversation log
- User says "process these notes" or "add this to Basic Memory"
- User pastes a document, article, or email for knowledge extraction
- Any time raw external text needs to become structured knowledge

## Workflow Overview

```
1. Parse raw input           → identify structure, extract key info
2. Extract entities          → people, orgs, topics, action items
3. Search existing entities  → multi-variation queries
4. Research new entities     → optional web research (see memory-research)
5. Present entity proposal   → get approval before creating
6. Create source note        → verbatim content + observations + relations
7. Create approved entities  → structured notes for each new entity
8. Extract action items      → follow-ups and commitments
```

## Step 1: Parse Raw Input

Read the pasted content and identify its structure:

- **Format**: Meeting transcript, email thread, conversation log, article, freeform notes
- **Date**: When this happened (extract from content or ask)
- **Participants**: Who was involved (names, roles, organizations)
- **Sections**: Any existing structure (headings, speaker labels, timestamps)

Don't rewrite or summarize the source content. Preserve it verbatim in the note — you'll add structured observations alongside it.

## Step 2: Extract Entities

Scan the content for entities worth tracking in the knowledge graph:

| Entity Type | Signals |
|-------------|---------|
| **Person** | Names with roles, titles, or affiliations mentioned |
| **Organization** | Company names, agencies, institutions |
| **Topic/Concept** | Technical domains, methodologies, standards discussed substantively |
| **Action Item** | Commitments, deadlines, "I'll do X by Y" statements |

**Infer type from context.** If someone is introduced as "CTO of Acme Corp", that's both a Person and an Organization entity. If a technology is discussed in depth, it might warrant a Concept entity.

**Exclude noise.** Not every name mentioned is worth an entity. Filter for:
- People with substantive roles or interactions (not passing mentions)
- Organizations discussed in business/technical context
- Topics with enough detail to warrant their own note

## Step 3: Search Existing Entities

For each extracted entity, search Basic Memory with multiple query variations:

```python
# Person — try full name, last name
search_notes(query="Sarah Chen")
search_notes(query="Chen")

# Organization — try full name, abbreviation, acronym
search_notes(query="National Renewable Energy Laboratory")
search_notes(query="NREL")

# Topic — try the full term and keywords
search_notes(query="edge computing")
search_notes(query="edge inference")
```

Classify each entity as:
- **Existing** — found in Basic Memory. Will link to it with `[[wiki-link]]`.
- **Proposed** — not found. Will propose creation pending approval.

## Step 4: Research New Entities (Optional)

For proposed entities where more context would be valuable, do a brief web search (2-3 queries max per entity):

- **Organizations**: What they do, size, public/private, key products
- **People**: Current role, background, expertise
- **Topics**: Brief definition, relevance

Use hedging language ("appears to be", "estimated", "based on public information"). Never fabricate details.

This step is optional — skip it if the source material provides enough context, or if the user is in a hurry. See the **memory-research** skill for deeper research workflows.

## Step 5: Present Entity Proposal

Before creating anything, present what you found and what you'd like to create:

```
Entities found in Basic Memory:
  - [[Sarah Chen]] (Person — existing)
  - [[Acme Corp]] (Organization — existing)

Proposed new entities:
  - Jordan Rivera (Person — VP Engineering at NovaTech, mentioned as project lead)
  - NovaTech (Organization — SaaS platform, Series B, discussed as integration partner)
  - Federated Learning (Concept — core technical topic of the discussion)

Approve all / select individually / skip entity creation?
```

Include enough context with each proposed entity for the user to make a quick decision.

## Step 6: Create the Source Note

Create the primary note for the ingested content. This is the "record of what happened" — it preserves the raw material and adds structured metadata.

### Meeting / Conversation Note

```python
write_note(
  title="NovaTech Meeting - Jordan Rivera - Feb 22, 2026",
  directory="meetings/2026",
  note_type="meeting",
  tags=["meeting", "novatech", "federated-learning"],
  metadata={"date": "2026-02-22"},
  content="""
# NovaTech Meeting - Jordan Rivera - Feb 22, 2026

Brief one-sentence summary of what this meeting was about.

## Transcript
[Preserve all source content verbatim — do not summarize or rewrite]

## Observations
- [opportunity] NovaTech interested in integration partnership
- [insight] Their platform handles 10K concurrent sessions, relevant to our scale needs
- [next_step] Send technical spec document by Friday
- [sentiment] Strong enthusiasm from their engineering team
- [decision] Agreed to start with a proof-of-concept integration

## Relations
- attended [[Jordan Rivera]]
- with [[NovaTech]]
- discussed [[Federated Learning]]
- follow_up [[Send NovaTech Technical Spec]]
"""
)
```

### Document / Article Note

```python
write_note(
  title="Edge Computing Architecture Whitepaper",
  directory="references",
  note_type="reference",
  tags=["edge-computing", "architecture", "reference"],
  metadata={"source": "https://example.com/whitepaper.pdf", "date_ingested": "2026-02-22"},
  content="""
# Edge Computing Architecture Whitepaper

## Source Content
[Preserve relevant content — for long documents, include key sections rather than the entire text]

## Observations
- [key_finding] Latency drops 40% with edge inference vs cloud-only
- [technique] Model sharding across heterogeneous edge nodes
- [limitation] Requires minimum 8GB RAM per edge node

## Relations
- relates_to [[Edge Computing]]
- relates_to [[Model Optimization]]
"""
)
```

### Observation Categories

Use categories that capture the nature of the information. Common categories for ingested content:

| Category | Use For |
|----------|---------|
| `opportunity` | Business or collaboration opportunities identified |
| `decision` | Decisions made or agreed upon |
| `insight` | Non-obvious understanding gained |
| `next_step` | Concrete action items or follow-ups |
| `sentiment` | Enthusiasm, concerns, hesitations expressed |
| `risk` | Risks or concerns identified |
| `requirement` | Requirements or constraints discovered |
| `key_finding` | Important facts from reference material |
| `technique` | Methods, approaches, or patterns described |
| `context` | Background information that may be useful later |

Invent categories as needed — these are suggestions, not a fixed list.

## Step 7: Create Approved Entities

For each entity the user approved, create a structured note. Match the entity type to an appropriate template.

### Person

```python
write_note(
  title="Jordan Rivera",
  directory="people",
  note_type="person",
  tags=["person", "novatech", "engineering"],
  content="""
# Jordan Rivera

## Overview
VP of Engineering at NovaTech. Met during integration partnership discussion.

## Background
[Role, expertise, context from meeting + any web research]

## Observations
- [role] VP Engineering at NovaTech
- [expertise] Distributed systems, federated learning
- [met] 2026-02-22 during integration discussion

## Relations
- works_at [[NovaTech]]
- discussed_in [[NovaTech Meeting - Jordan Rivera - Feb 22, 2026]]
"""
)
```

### Organization

```python
write_note(
  title="NovaTech",
  directory="organizations",
  note_type="organization",
  tags=["organization", "saas", "integration-partner"],
  content="""
# NovaTech

## Overview
SaaS platform company. Series B stage.
[Additional context from meeting + web research]

## Products & Services
[What they offer, if discussed or researched]

## Observations
- [stage] Series B, ~200 employees
- [relevance] Potential integration partner for our platform
- [first_contact] 2026-02-22

## Relations
- employs [[Jordan Rivera]]
- discussed_in [[NovaTech Meeting - Jordan Rivera - Feb 22, 2026]]
"""
)
```

### Concept / Topic

```python
write_note(
  title="Federated Learning",
  directory="concepts",
  note_type="concept",
  tags=["concept", "machine-learning", "distributed-systems"],
  content="""
# Federated Learning

## Overview
[Brief description of the concept from the discussion context]

## Observations
- [definition] Machine learning approach where models train across decentralized data sources
- [relevance] Core technique discussed in NovaTech integration

## Relations
- discussed_in [[NovaTech Meeting - Jordan Rivera - Feb 22, 2026]]
"""
)
```

Adapt templates to your domain. The key elements are: type and tags as parameters, an overview section, observations with categories, and relations linking back to the source.

## Step 8: Extract Action Items

Review the source content for commitments and follow-ups:

```
Action Items:
  - Send NovaTech technical spec document by Friday (your commitment)
  - Jordan will share their API documentation by next week (their commitment)

Follow-Up Reminders:
  - 1 week: Check if Jordan sent API docs
  - 2 weeks: Schedule follow-up call to discuss POC scope
```

If using the **memory-tasks** skill, create Task notes for your action items. Otherwise, capture them as observations in the source note.

## Guidelines

- **Preserve source content verbatim.** The original text is the ground truth. Structure and observations are your interpretation layered on top.
- **Search before creating.** Always check if entities already exist (see memory-notes search-before-create pattern). Update existing entities with new information rather than creating duplicates.
- **Get approval for new entities.** Present proposed entities and let the user decide which to create. Don't silently populate the knowledge graph.
- **Infer, don't interrogate.** Extract entity types and relationships from context. Only ask the user when genuinely ambiguous.
- **Be selective about entities.** Not every name mentioned deserves its own note. Focus on entities the user will want to reference again.
- **Use hedging for researched info.** Web research supplements — don't present it as fact. "Appears to be", "estimated", "based on public information".
- **Link everything back.** Every created entity should relate back to the source note. The source note should link to all entities discussed.
- **Prose and observations together.** Notes work best with both narrative context and structured observations. Prose gives meaning and tells the story; observations make individual facts searchable. Use the body for context, then distill key facts into categorized observations.
