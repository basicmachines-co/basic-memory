# Cloud Semantic Search Value (Customer-Facing Technical Story)

This document explains why teams should buy cloud semantic search even when local search exists.

## Core Promise

Markdown files remain the source of truth in both local and cloud modes.

- Files are portable.
- Search indexes are derived and rebuildable.
- You never get locked into proprietary document storage.

## The Customer Problem

Teams paying for cloud are usually not optimizing for "can this run locally." They are optimizing for:

- finding the right note the first time,
- keeping retrieval quality high as note volume grows,
- avoiding search slowdowns while content is actively changing,
- getting consistent results across users, agents, and sessions.

## Why Cloud Is the Aspirin

Cloud semantic search is the immediate pain reliever because it fixes the problems users feel right now.

### 1) Better hit rate on real queries

Cloud uses stronger managed embeddings than the default local model, which improves semantic recall for paraphrases and vague questions.

Customer outcome:

- fewer "I know this exists but search missed it" moments,
- less query rewording,
- faster time to answer.

### 2) Better behavior under active workloads

Cloud indexing runs out of band in workers, so indexing does not compete with interactive read/write traffic.

Customer outcome:

- stable search responsiveness during heavy updates,
- fresher semantic results shortly after edits,
- less user-visible performance variance.

### 3) Better consistency for shared knowledge

Cloud retrieval runs against a centralized tenant index, so teams and agents resolve against the same semantic state.

Customer outcome:

- fewer "works on my machine" search differences,
- more predictable agent behavior across environments,
- easier cross-user collaboration on large knowledge bases.

### 4) Better quality at higher scale

With Postgres + `pgvector` per tenant, cloud can sustain larger note collections and higher query volumes than typical local setups.

Customer outcome:

- confidence as repositories grow to tens of thousands of notes,
- less need for user-side tuning,
- fewer quality regressions as usage increases.

## Local Is the Vitamin

Local semantic search still matters and should stay strong.

- offline use,
- privacy-first operation,
- no cloud dependency,
- user-controlled runtime.

It compounds long-term ownership and resilience, but does not remove the immediate pain points cloud solves for teams at scale.

## Recommended Messaging

One-liner:

"Cloud semantic search is the aspirin: it fixes retrieval quality and performance pain now. Local semantic search is the vitamin: it builds long-term control and resilience."

Long form:

"Basic Memory keeps markdown as the source of truth everywhere. Local gives privacy and offline control. Cloud adds immediate, measurable improvements in search quality, consistency, and responsiveness for teams and agents running at scale."

## Packaging Guidance

- Base: local FTS plus optional local semantic search.
- Cloud value: higher semantic quality, stable performance under load, and consistent team-wide retrieval.
- Keep interfaces pluggable (`EmbeddingProvider`, vector backend protocol) so implementation can evolve without changing user workflows.
