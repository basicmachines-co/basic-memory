# SPEC-LOCAL-GRAPH-INTELLIGENCE-IMPLEMENTATION-PLAN

**Status:** Draft (Decision-Complete)  
**Date:** 2026-03-05  
**Owner:** Basic Memory Engineering  
**Implementation Status (2026-03-05):** Phase 1 contract skeleton implemented in `basic-memory` branch `codex/graph-intelligence-phase1`.  
**Related Specs:**
1. `/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE-MASTER.md`
2. `/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE-TECHNICAL-ADDENDUM.md`
3. `/docs/specs/SPEC-LOCAL-GRAPH-INTELLIGENCE.md`

## Scope and Intent

This document is the execution handoff for Local+ Graph Intelligence.

It defines exactly how we will deliver graph and FCM capabilities inside the existing Basic Memory architecture:
1. FastAPI-first business logic.
2. MCP and CLI as thin facades.
3. Local/cloud contract parity.
4. Tight build-test-iterate loop for fast delivery.

This file is intentionally implementation-oriented and does not duplicate pricing narrative from the master spec.

## Architecture Alignment (FastAPI-first, MCP/CLI facade)

Locked architecture alignment for implementation:
1. MCP tools remain thin proxy facades.
2. CLI `bm tool` commands call MCP tools in JSON mode.
3. Core logic lives in FastAPI routers and services.
4. Cloud and local share the same REST contracts.
5. Per-project routing continues through existing project client patterns.

Execution mapping:
1. API routers define public contracts in `/graph` and `/fcm` domains.
2. Services own traversal, scoring, simulation, and fallback logic.
3. Repositories and index providers own data access and graph index operations.
4. MCP typed clients call REST endpoints and return JSON-first tool output.
5. CLI passthrough executes tool calls and prints machine-friendly JSON.

## Locked Decisions

1. SQLite remains operational source for entities, relations, embeddings, and project state.
2. Markdown remains source of truth.
3. Oxigraph/pyoxigraph is the derived graph index for deep traversal.
4. FCM simulation runs in Python service layer; it is not delegated to graph DB query engines.
5. Graph index is rebuildable and disposable; stale index never blocks user workflows.
6. FCM model and scenario artifacts persist in app database.
7. Local+ features are gated by config flags first; entitlement wiring follows later.
8. Graph-first vertical slices ship before deep FCM expansion.
9. Atomic tools ship first; orchestration workflows are deferred.
10. Existing `build_context` and `search_notes` remain backward compatible with no breaking change.

## Progress Snapshot (as of 2026-03-05)

Completed in Phase 1:
1. Added `/graph` and `/fcm` v2 routers with all required contract endpoints.
2. Added graph/FCM request and response schemas for all public API contracts.
3. Added service-layer implementations for graph and FCM contract endpoints.
4. Added typed MCP clients for graph and FCM API calls.
5. Added MCP tools: `graph_lineage`, `graph_impact`, `graph_health`, `graph_reindex`, `fcm_simulate`, `fcm_rank_actions`, `fcm_import_model`, `fcm_export_model`.
6. Added CLI passthrough commands under `bm tool ...` for all planned graph/FCM operations.
7. Added scheduler task names for graph lifecycle: `sync_graph_entity`, `sync_graph_project`, `reindex_graph_project`.
8. Added focused tests for API, MCP clients/tools, and CLI graph/FCM passthrough.
9. Added fast-loop `just` targets: `test-graph-intel-api`, `test-graph-intel-mcp`, `test-graph-intel-cli`, `test-graph-intel`.

Validation completed:
1. `just test-graph-intel` passes.
2. `ruff check` passes on changed files.
3. `pyright` passes on changed files.

Still pending after Phase 1:
1. SQL-backed traversal/scoring for graph `lineage`, `impact`, and `health`.
2. Oxigraph provider integration and stale-index catch-up flow.
3. Persistent FCM model/scenario state and interop round-trip guarantees.
4. Config-flag and entitlement gating at API/tool boundaries.
5. Performance instrumentation and p95 envelope enforcement.

## Delivery Phases

### Phase 1: Contract skeleton

Status: Completed (2026-03-05)

Deliverables:
1. Add `/graph` and `/fcm` API routers with request/response schemas.
2. Add typed MCP clients for graph and FCM endpoints.
3. Add MCP tool passthrough commands for all new operations.
4. Add CLI `bm tool` passthrough commands mirroring MCP surface.
5. Add minimal smoke tests for route reachability and schema validation.

Exit criteria:
1. All endpoints return structured success and error envelopes.
2. MCP/CLI paths execute end-to-end with stubbed service responses.

### Phase 2: Graph capabilities on SQL-backed logic

Status: Next active phase

Deliverables:
1. Implement `lineage`, `impact`, and `health` in service layer using SQL-backed traversal and scoring.
2. Add provenance/evidence linking in graph outputs.
3. Add deterministic graph-health calculations for fixed snapshots.

Exit criteria:
1. `graph_lineage`, `graph_impact`, and `graph_health` pass contract tests.
2. SQL fallback behavior is explicit and covered by tests.

### Phase 3: Oxigraph derived index provider

Status: Planned

Deliverables:
1. Introduce Oxigraph provider behind graph-query interface.
2. Add lazy catch-up jobs and project-wide reindex operation.
3. Preserve SQL fallback when index is missing or stale.

Exit criteria:
1. Stale index path serves results via SQL and schedules catch-up.
2. Index rebuild can be triggered and completed without data loss.

### Phase 4: FCM import/simulate/rank/export

Status: Planned (contract endpoints complete, full behavior pending)

Deliverables:
1. Implement CSV-first import/export contracts.
2. Implement deterministic simulation core with convergence metadata.
3. Implement action ranking with evidence references and confidence output.
4. Persist scenario inputs and result artifacts.

Exit criteria:
1. Research flow scenario passes: import -> simulate -> rank -> export.
2. Interop round-trip preserves node/edge counts and signed weights.

### Phase 5: Hardening

Status: Planned

Deliverables:
1. Performance tuning against published latency envelopes.
2. Local/cloud parity tests for semantics and error behavior.
3. MCP prompt/docs updates for new graph and FCM tools.
4. Operational docs for reindex, fallback, and troubleshooting.

Exit criteria:
1. `just check` passes before merge.
2. Acceptance criteria in this document are fully met.

## API and Interface Additions

### Shared API conventions

1. All endpoints are project-scoped under `/v2/projects/{project_id}`.
2. Request and response bodies are JSON-first and agent-friendly.
3. Success envelope is endpoint-specific payload with deterministic fields and optional probabilistic fields.
4. Error envelope:
```json
{
  "error": {
    "code": "INVALID_ARGUMENT|NOT_FOUND|INDEX_NOT_READY|MODEL_INVALID|RESOURCE_LIMIT_EXCEEDED|INTERNAL_ERROR",
    "message": "string",
    "details": {}
  }
}
```
5. Latency and scale targets are p95 targets for local default hardware profile.

### 1) `POST /v2/projects/{project_id}/graph/lineage`

Purpose: explain decision lineage and supporting evidence paths.

Request schema:
```json
{
  "start": "string",
  "goal": "string|null",
  "max_hops": 4,
  "relation_filters": ["string"]
}
```

Response schema:
```json
{
  "root": {"id": "string", "title": "string", "permalink": "string"},
  "paths": [
    {
      "path_id": "string",
      "nodes": [{"id": "string", "title": "string"}],
      "edges": [{"relation": "string", "direction": "outgoing|incoming"}],
      "deterministic_path_score": 0.0,
      "confidence": 0.0,
      "evidence_refs": ["memory://..."]
    }
  ],
  "generated_at": "RFC3339"
}
```

Deterministic fields: `root`, `paths.nodes`, `paths.edges`, `deterministic_path_score`, `generated_at`.  
Probabilistic fields: `confidence`.  
Latency target: p95 <= 450ms with `max_hops<=4`.  
Scale envelope: up to 50k nodes and 300k edges.

### 2) `POST /v2/projects/{project_id}/graph/impact`

Purpose: preview impact radius before edits or decisions.

Request schema:
```json
{
  "target": "string",
  "horizon": 2,
  "relation_filters": ["string"],
  "include_reasons": true
}
```

Response schema:
```json
{
  "target": {"id": "string", "title": "string"},
  "affected": [
    {
      "id": "string",
      "title": "string",
      "distance": 1,
      "impact_score": 0.0,
      "confidence": 0.0,
      "reasons": ["string"],
      "evidence_refs": ["memory://..."]
    }
  ],
  "summary": {"total_considered": 0, "total_returned": 0}
}
```

Deterministic fields: membership, distance, summary counts.  
Probabilistic fields: `impact_score`, `confidence`.  
Latency target: p95 <= 650ms for `horizon<=3`.  
Scale envelope: default 200 results, hard cap 1000 with pagination token.

### 3) `GET /v2/projects/{project_id}/graph/health`

Purpose: report deterministic graph quality and actionable issues.

Query params:
1. `scope` optional directory prefix.
2. `timeframe` optional window like `30d`.

Response schema:
```json
{
  "metrics": {
    "orphan_rate": 0.0,
    "stale_central_nodes": 0,
    "overloaded_hubs": 0,
    "contradiction_candidates": 0
  },
  "issues": [
    {
      "issue_type": "orphan|stale_central|overloaded_hub|contradiction_candidate",
      "entity_id": "string",
      "severity": "low|medium|high",
      "reason": "string",
      "suggested_action": "string",
      "confidence": 0.0
    }
  ],
  "computed_at": "RFC3339"
}
```

Deterministic fields: `metrics`, issue membership for fixed snapshot.  
Probabilistic fields: contradiction confidence when applicable.  
Latency target: p95 <= 1500ms project-wide, <= 700ms scoped.

### 4) `POST /v2/projects/{project_id}/graph/reindex`

Purpose: force project-wide graph index rebuild.

Request schema:
```json
{
  "mode": "full|incremental",
  "reason": "string|null"
}
```

Response schema:
```json
{
  "job_id": "string",
  "status": "queued|running|completed|failed",
  "scheduled_at": "RFC3339"
}
```

Deterministic fields: job metadata and status transitions.  
Probabilistic fields: none.  
Latency target: enqueue response p95 <= 120ms.

### 5) `POST /v2/projects/{project_id}/fcm/simulate`

Purpose: run FCM scenario simulation.

Request schema:
```json
{
  "actions": [{"node_id": "string", "delta": 0.2}],
  "scenario": {
    "steps": 12,
    "activation": "tanh|sigmoid|bounded_linear",
    "decay": 0.05
  },
  "clamp_rules": [{"node_id": "string", "min": -1.0, "max": 1.0}]
}
```

Response schema:
```json
{
  "baseline": [{"node_id": "string", "state": 0.0}],
  "projected": [{"node_id": "string", "state": 0.0}],
  "deltas": [{"node_id": "string", "delta": 0.0}],
  "stability": {"converged": true, "iterations_used": 0, "residual": 0.0},
  "confidence": 0.0,
  "explanations": [{"node_id": "string", "top_influencers": [{"source": "string", "weight": 0.0}]}],
  "evidence_refs": ["memory://..."]
}
```

Deterministic fields: baseline, projected, deltas, stability for fixed model and params.  
Probabilistic fields: confidence.  
Latency target: p95 <= 1000ms for <=500 nodes and <=5000 edges.

### 6) `POST /v2/projects/{project_id}/fcm/rank-actions`

Purpose: rank candidate interventions by expected outcome and risk.

Request schema:
```json
{
  "goal": "string",
  "constraints": {
    "max_negative_impact": 0.25,
    "required_tags": ["string"],
    "disallowed_nodes": ["string"]
  },
  "top_k": 10
}
```

Response schema:
```json
{
  "goal": {"node_id": "string", "label": "string"},
  "recommendations": [
    {
      "action_node_id": "string",
      "expected_goal_delta": 0.0,
      "risk_penalty": 0.0,
      "net_score": 0.0,
      "confidence": 0.0,
      "rationale": ["string"],
      "evidence_refs": ["memory://..."]
    }
  ]
}
```

Deterministic fields: candidate set and constraint compliance.  
Probabilistic fields: expected delta, penalty, net score, confidence.  
Latency target: p95 <= 1500ms for top-10 from <=100 candidates.

### 7) `POST /v2/projects/{project_id}/fcm/import`

Purpose: import FCM model from CSV-first contract.

Request schema:
```json
{
  "source": "string",
  "format": "csv_bundle_v1",
  "merge_mode": "replace|upsert"
}
```

Response schema:
```json
{
  "import_id": "string",
  "nodes_loaded": 0,
  "edges_loaded": 0,
  "warnings": ["string"],
  "errors": ["string"]
}
```

Deterministic fields: counts and validation diagnostics.  
Probabilistic fields: none.  
Latency target: p95 <= 2500ms for 10k edges import.

### 8) `POST /v2/projects/{project_id}/fcm/export`

Purpose: export FCM model for interoperability.

Request schema:
```json
{
  "format": "csv_bundle_v1",
  "selection": {
    "scope": "all|tag|subgraph",
    "tag": "string|null",
    "seed_nodes": ["string"]
  }
}
```

Response schema:
```json
{
  "export_id": "string",
  "format": "csv_bundle_v1",
  "files": [{"name": "nodes.csv", "path": "string"}, {"name": "edges.csv", "path": "string"}],
  "node_count": 0,
  "edge_count": 0
}
```

Deterministic fields: file names and counts for fixed selection.  
Probabilistic fields: none.  
Latency target: p95 <= 1800ms for 50k edges export.

## Data Model and Storage Boundaries

1. SQLite is mandatory operational source for entities, relations, embeddings, and project metadata.
2. Oxigraph stores derived knowledge graph index only.
3. FCM state persists in app database with scenario artifacts and run history.
4. Graph index is rebuildable and disposable by design.
5. Markdown files remain canonical source of truth.

Implementation data boundaries:
1. Knowledge graph schema tracks descriptive nodes and typed edges plus provenance.
2. FCM schema tracks signed weighted causal edges and node states.
3. Provenance model requires `evidence_refs`, `confidence`, and `updated_at`.
4. Scenario model stores interventions, constraints, run parameters, and output deltas.
5. Interop schema starts with CSV-first Mental Modeler contract.

## Background Jobs and Index Lifecycle

Scheduler tasks to add:
1. `sync_graph_entity`
2. `sync_graph_project`
3. `reindex_graph_project`

Lifecycle rules:
1. Note writes, edits, moves, and deletes schedule graph-index sync tasks.
2. Scheduling pattern mirrors existing vector sync behavior.
3. On stale or missing graph index, request path serves via SQL fallback and schedules catch-up.
4. Reindex is idempotent and safe to rerun.
5. Index version metadata is tracked per project for staleness checks.

Operational behaviors:
1. Foreground requests never block on full reindex completion.
2. Background job failures surface in health endpoints with actionable status.
3. Reindex job can run incremental or full mode.
4. Phase 1 note: scheduler task names and reindex enqueue path are implemented; write/edit/move/delete sync hooks still need explicit wiring.

## MCP and CLI Surface

New MCP tools:
1. `graph_lineage`
2. `graph_impact`
3. `graph_health`
4. `fcm_simulate`
5. `fcm_rank_actions`
6. `fcm_import_model`
7. `fcm_export_model`

CLI passthrough additions:
1. `bm tool graph-lineage ...`
2. `bm tool graph-impact ...`
3. `bm tool graph-health ...`
4. `bm tool fcm-simulate ...`
5. `bm tool fcm-rank-actions ...`
6. `bm tool fcm-import-model ...`
7. `bm tool fcm-export-model ...`

Output conventions:
1. Default output is JSON for MCP and CLI.
2. MCP supports optional `output_format="text"` for human-readable summaries.
3. CLI remains JSON-first to keep agent integration deterministic.

## Test Strategy (fast loop + gates)

### Slice-by-slice loop

For each vertical slice, implement in this order:
1. API contract and schema.
2. Typed MCP client.
3. MCP tool passthrough.
4. CLI passthrough.
5. Focused tests for API/MCP/CLI.

Fast checks per slice:
1. Targeted `pytest` for changed API, MCP, and CLI modules.
2. `just fast-check`.
3. `just doctor`.
4. `just test-graph-intel` for graph/FCM-only iteration loop.

Milestone gates:
1. SQLite unit and integration pass first.
2. Selective Postgres parity tests for new graph and FCM contracts.
3. Full `just check` before merge.

### Required test cases and scenarios

1. Casual user impact preview before note edit.
2. Decision audit: lineage plus evidence references explain recommendation.
3. Graph health deterministic output for fixed snapshot.
4. Research flow: import model -> simulate -> rank -> export.
5. Sparse and contradictory graph input degrades gracefully.
6. Interop round-trip preserves node/edge counts and signed weights.
7. Local and cloud parity on contract semantics and error model.
8. Stale index fallback path returns valid response and schedules catch-up.

## Rollout and Feature Flagging

Rollout controls:
1. Gate graph and FCM endpoints behind config flags first.
2. Add entitlement enforcement after behavior and reliability stabilize.
3. Keep existing tools and endpoints fully backward compatible.

Suggested flags:
1. `feature_graph_intelligence_enabled`
2. `feature_fcm_enabled`
3. `feature_graph_oxigraph_provider_enabled`
4. `feature_graph_sql_fallback_enabled`

Rollout sequence:
1. Enable contract skeleton in dev.
2. Enable graph features for internal alpha users.
3. Enable Oxigraph provider with fallback-on by default.
4. Enable FCM import/simulate/rank/export for research alpha users.
5. Promote to Local+ beta when acceptance criteria are met.

## Risks and Mitigations

1. Risk: graph query complexity increases p95 latency.  
Mitigation: strict query caps, fallback path, and performance budgets per endpoint.
2. Risk: stale index produces confusing outputs.  
Mitigation: explicit staleness checks, SQL fallback, and background catch-up scheduling.
3. Risk: FCM recommendations appear opaque.  
Mitigation: require evidence references, confidence fields, and deterministic simulation metadata.
4. Risk: local/cloud contract drift.  
Mitigation: shared schemas, contract tests, and parity checks in CI gates.
5. Risk: integration surface grows faster than team can validate.  
Mitigation: phase gates and vertical-slice completion before opening next phase.

## Improvement Backlog (Post-Phase 1)

1. Refactor `bm tool` graph/FCM commands into a dedicated CLI module to reduce `tool.py` size and improve maintainability.
2. Consolidate repeated MCP text-formatting helpers for graph/FCM outputs.
3. Replace deterministic placeholder graph behavior with SQL-backed lineage/impact/health implementations.
4. Add explicit config/entitlement enforcement for graph/FCM endpoints and tools.
5. Add performance telemetry and p95 reporting for graph and FCM routes.
6. Add parity and degradation tests for stale-index fallback and contradictory/sparse inputs.

## Acceptance Criteria

1. All required sections in this document are complete with no unresolved decisions.
2. API/interface contracts are implementation-ready with request, response, error, latency, and scale details.
3. Architecture alignment is explicit: FastAPI logic core, MCP/CLI facades, shared local/cloud contracts.
4. Delivery phases define concrete outputs and exit criteria.
5. Test strategy includes tight iteration loop and milestone gates.
6. Required scenario matrix is covered in test plan and mapped to implementation phases.
7. Rollout plan includes feature flags and backward compatibility guarantees.
8. An implementer can execute this plan without additional architecture clarification.

## Assumptions and Defaults

1. Config-flag gating first; entitlement wiring later.
2. Graph-first vertical slices before deep FCM expansion.
3. Atomic tools first; orchestration layer deferred.
4. JSON-first contracts for agent usability.
5. No breaking changes to existing `build_context` and `search_notes`.

## Out of Scope

1. Implementation details unrelated to graph/FCM delivery phases in this document.
2. Migration execution.
3. Pricing and positioning rewrites.
4. Cloud infrastructure changes in this phase.
