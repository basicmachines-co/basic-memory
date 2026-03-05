# SPEC-LOCAL-GRAPH-INTELLIGENCE: Technical Addendum (Graph + FCM)

**Status:** Draft  
**Date:** 2026-03-05  
**Owner:** Basic Memory
**Current Phase (2026-03-05):** Contract skeleton implementation is complete; next active phase is SQL-backed graph capabilities.

Related product spec:
`/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE.md`

Related execution spec:
`/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE-IMPLEMENTATION-PLAN.md`

## Why This Addendum Exists

The product spec defines user value. This addendum defines the technical shape that can deliver that value without
breaking local-first principles.

This addendum also introduces a second graph layer:

1. Knowledge graph for relationships between notes, entities, and decisions.
2. Fuzzy Cognitive Model (FCM) graph for weighted causal reasoning over actions and outcomes.

Both are derived from markdown and optional user-provided models.

## Strategic Reality Check

This is a strong idea if we stage it correctly.

It is not a pipe dream if we avoid one trap: building a big "graph platform" before proving users repeatedly use
decision simulation workflows.

The correct strategy is:

1. Launch high-precision graph insights first.
2. Add FCM scoring where it changes user behavior (not as a novelty dashboard).
3. Expand to hosted/team workflows only after local usage proves repeat value.

## Constraints and Design Principles

1. SQLite remains the operational source for entities, observations, relations, and embeddings.
2. Markdown remains source of truth.
3. Graph indexes are derived, rebuildable, and disposable.
4. Premium local mode must run fully offline.
5. Cloud deployment should support both single-tenant and SaaS later.
6. Avoid licenses that constrain hosted/open-source strategy.

## Backend Recommendation

### Primary Recommendation

Use a dual-store architecture:

1. SQLite (existing): operational data, metadata filters, embeddings, and most retrieval.
2. Oxigraph/pyoxigraph (new): derived graph index for graph traversal and graph-pattern queries.
3. Python simulation layer (new): FCM state propagation, scenario runs, and decision scoring.

Why this is the best fit:

1. Permissive licensing profile.
2. Works locally with low footprint.
3. Cloud-compatible as sidecar service while keeping Neon Postgres as core cloud store.
4. Clear boundary between graph query and numeric simulation concerns.

### Candidate Trade-Offs

#### Oxigraph/pyoxigraph

Pros:

1. Lightweight local embedding.
2. Good fit for derived-index strategy.
3. Strong path for standards-based graph representation.

Cons:

1. SPARQL fluency is less common than SQL/Cypher.
2. Requires a translation layer so product features are not query-language-coupled.

#### Apache AGE (Postgres extension)

Pros:

1. SQL + graph in one engine.
2. Attractive for cloud-side graph operations.

Cons:

1. Neon support is uncertain for this extension.
2. Local/cloud parity is harder if local uses SQLite.

#### SurrealDB / FalkorDB

Pros:

1. Strong graph-oriented developer experience.

Cons:

1. License posture is misaligned with a future hosted/open-source roadmap unless commercial terms are accepted.

Decision:
Do not make these core dependencies for v1 of Local+ Graph Intelligence.

## Two-Graph Model

### A) Knowledge Graph (Descriptive)

Node examples:

1. Note
2. Decision
3. Spec
4. Person
5. Project
6. Concept

Edge examples:

1. `depends_on`
2. `informed_by`
3. `contradicts`
4. `supports`
5. `implements`
6. `derived_from`

Purpose:
Power navigation, lineage, path explanation, impact radius, and health checks.

### B) FCM Graph (Causal, Signed, Weighted)

Node examples:

1. Goal: "Reduce regressions"
2. Driver: "Test coverage"
3. Risk: "Scope creep"
4. Intervention: "Add review gate"
5. Context variable: "Team bandwidth"

Edge attributes:

1. `weight` in [-1.0, 1.0]
2. `confidence` in [0.0, 1.0]
3. `evidence_refs` (links to notes/specs)
4. `time_decay` (optional)

Purpose:
Power scenario simulation and action ranking, not generic retrieval.

## Premium Feature Mapping to Architecture

### Decision Lineage

Backed by:

1. Knowledge graph path queries.
2. Evidence references stored on edges.

### Impact Radius

Backed by:

1. Multi-hop neighborhood expansion with relation-type weights.
2. Risk ranking using centrality + recency + confidence.

### Contradiction Watch

Backed by:

1. Candidate contradiction edges.
2. Confidence-scored reconciliation queue.

### Priority Briefs

Backed by:

1. Health metrics (orphan rate, stale-central nodes, unresolved contradictions).
2. Optional FCM "top leverage actions" summary.

### New Premium Feature: Action Simulator

Backed by:

1. FCM scenario runs over selected action nodes.
2. Ranked interventions with expected positive/negative downstream effects.
3. Explicit rationale graph for every recommendation.

## Mental Modeler Interop Plan

Goal:
Make Basic Memory the AI-enabled operating layer around existing researcher workflows, not a replacement for their tools.

Interoperability phases:

1. Import/export edge lists and node tables via CSV as the baseline interchange.
2. Preserve concept IDs and metadata so round-trips remain stable.
3. Add translator support for native model files if/when schema contracts are validated with partner data.

Validation requirement:

1. Round-trip tests must preserve node count, edge count, and signed weights.
2. Confidence/evidence metadata may be Basic Memory extensions and should degrade gracefully when exported.

## Suggested Tool/API Surface (Product-Facing)

1. `graph_lineage(start, goal?)`  
   Returns explainable evidence paths.
2. `graph_impact(target, horizon=2..4)`  
   Returns ranked affected nodes with reasons.
3. `graph_health()`  
   Returns actionable graph quality issues.
4. `fcm_simulate(actions, scenario?)`  
   Returns projected effects and uncertainty.
5. `fcm_rank_actions(goal, constraints?)`  
   Returns top candidate actions with trade-offs.
6. `fcm_import_model(source)` / `fcm_export_model(format)`  
   Handles interop with external cognitive mapping workflows.

## Local and Cloud Deployment Shape

### Local (Primary)

1. SQLite + local embeddings.
2. Oxigraph as local sidecar/index library.
3. FCM simulation in process.

### Cloud (Future-Compatible)

1. Neon Postgres remains system of record in hosted mode.
2. Graph index service runs per tenant or shared multi-tenant with strict tenancy boundaries.
3. FCM simulation service can run stateless workers reading graph snapshots.

Principle:
Do not require cloud to run premium local features.

## Rollout Plan With Go/No-Go Gates

### Phase 0: Proof of Utility (4-6 weeks)

Deliver:

1. Decision Lineage
2. Impact Radius
3. CSV FCM import + `fcm_simulate` prototype

Gate to continue:

1. Repeated weekly usage by pilot users.
2. Users report changed decisions, not just curiosity clicks.

### Phase 1: Productized Local+ Beta

Deliver:

1. Graph health workflow
2. Contradiction Watch
3. Action ranking with explicit rationale

Gate to continue:

1. Retention of graph features after first month.
2. Measured reduction in "surprise side effects" after edits.

### Phase 2: Hosted Expansion

Deliver:

1. Optional cloud execution for heavy simulations.
2. Team-shared model governance.

Gate to continue:

1. Clear willingness to pay for hosted collaboration.

## Risks and Mitigations

Risk: FCM outputs feel "made up."  
Mitigation: Require evidence links and confidence scoring in every recommendation.

Risk: Research-heavy feature alienates casual users.  
Mitigation: Keep FCM features in an advanced mode; default to concise guidance workflows.

Risk: Overengineering early graph stack.  
Mitigation: Keep derived-index architecture and strict phase gates tied to behavior change.

Risk: Interop friction with external tooling.  
Mitigation: Start with transparent CSV contract and strict round-trip validation.

## Candid Recommendation

Pursue this. It is a high-upside differentiation path for Local+ if executed with staged validation.

The key is to sell outcomes:

1. "Safer decisions"
2. "Explainable recommendations"
3. "Faster synthesis for complex research"

Avoid selling "graph DB" as the product. That is implementation detail.
