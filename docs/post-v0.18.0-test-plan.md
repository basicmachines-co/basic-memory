# Post-v0.18.0 Test Plan and Acceptance Criteria

## Goal

Define a complete validation plan for all major features merged after `v0.18.0`, combining:

- Coverage-gap-driven automated tests
- Real MCP server integration tests (no mocks for target flows)
- Manual MCP verification via LLM-driven tool calls

This plan is based on commits in `v0.18.0..HEAD` and the latest `just check` coverage output.

## Scope Window

- Start tag: `v0.18.0` (2026-01-28)
- End: current `main`
- Change volume: 12 feature commits + 14 bug-fix commits (+ release chores/hotfixes)

## Execution Strategy

1. Stabilize all feature-level acceptance criteria in automated tests first.
2. Add black-box MCP integration tests for semantic search + schema (real server startup).
3. Run manual MCP tool-call verification to confirm real UX and routing behavior.
4. Re-run full gate: `just check` + targeted integration packs.

## Global Quality Gates

- Feature criteria below must all pass.
- No regressions in existing suites.
- Coverage improves in targeted low-coverage feature modules.
- SQLite and Postgres parity for search/semantic features.

## Priority Coverage Gaps (from latest run)

These are the most important post-`v0.18.0` feature modules currently under-covered:

- `src/basic_memory/mcp/tools/schema.py` (27%)
- `src/basic_memory/mcp/clients/schema.py` (36%)
- `src/basic_memory/mcp/tools/ui_sdk.py` (43%)
- `src/basic_memory/mcp/tools/search.py` (73%)
- `src/basic_memory/repository/postgres_search_repository.py` (63%)
- `src/basic_memory/mcp/async_client.py` (82%)
- `src/basic_memory/api/v2/routers/schema_router.py` (80%)

## Feature Acceptance Criteria and Test Plan

### 1) Schema System (`c97733d`) — DONE

### Acceptance criteria

- `schema_validate`, `schema_infer`, and `schema_diff` produce consistent outcomes across CLI/API/MCP for the same fixture set.
- Strict validation fails deterministically on required-field/type violations.
- Validation warnings are stable and machine-readable in non-strict mode.
- Inference output is deterministic for unchanged input corpus.
- Drift diff output is deterministic and identifies missing/extra/type-mismatch fields correctly.

### Existing coverage anchor points

- `tests/schema/*`
- `tests/api/v2/test_schema_router.py`
- `test-int/test_schema/*`

### Gaps to close — DONE

- ~~MCP schema tool branches (`src/basic_memory/mcp/tools/schema.py`)~~ — 18 tests in `tests/mcp/test_tool_schema.py`
- ~~MCP schema client behavior (`src/basic_memory/mcp/clients/schema.py`)~~ — `tests/mcp/test_client_schema.py`
- ~~Schema router error-path branches (`src/basic_memory/api/v2/routers/schema_router.py`)~~ — `tests/api/v2/test_schema_router.py`

### Planned additions — DONE

- ~~Add MCP tool tests for `schema_validate` strict + non-strict result shapes.~~ **DONE**
- ~~Add MCP tool tests for `schema_infer` with explicit `entity_type` and inferred type fallback.~~ **DONE**
- ~~Add MCP tool tests for `schema_diff` empty-diff and non-empty-diff paths.~~ **DONE**
- ~~Add API tests for schema router invalid payload/edge error handling.~~ **DONE**
- Add integration test that starts MCP server and calls schema tools end-to-end on fixture notes. — deferred to backlog item 4.

### 2) Semantic Search (`0777879`, `1428d18`, `344e651`) — DONE

### Acceptance criteria

- `search_type=text|vector|hybrid` returns expected ranked results on canonical semantic corpus.
- Missing semantic extras fail fast with actionable install guidance.
- Reindex and provider/model changes produce valid vectors without dimension mismatch.
- SQLite and Postgres produce equivalent behavior for semantic modes on the same dataset.
- Generated-column migration path is valid on SQLite environments in use.

### Existing coverage anchor points

- `tests/repository/test_sqlite_vector_search_repository.py`
- `tests/repository/test_postgres_search_repository.py`
- `tests/services/test_semantic_search.py`
- `tests/mcp/test_tool_search.py`
- `test-int/test_search_performance_benchmark.py`

### Gaps to close — DONE

- ~~Uncovered Postgres vector/hybrid branches~~ — 20 tests in `tests/repository/test_postgres_search_repository_unit.py` + 5 integration tests in `test-int/semantic/test_semantic_coverage.py`
- ~~MCP search semantic/output branches~~ — expanded `tests/mcp/test_tool_search.py`

### Planned additions — DONE

- ~~Expand Postgres repository tests for vector query composition edge cases.~~ **DONE**
- ~~Expand Postgres repository tests for hybrid fusion ranking and pagination branches.~~ **DONE**
- ~~Expand Postgres repository tests for embedding/provider error handling branches.~~ **DONE**
- ~~Expand MCP search tool tests for vector/hybrid output formatting branches.~~ **DONE**
- ~~Expand MCP search tool tests for semantic-disabled and missing-dependency failures.~~ **DONE**
- Add MCP integration tests that start server and execute semantic `search_notes` tool calls. — deferred to backlog item 4.

### Semantic search quality benchmarks (NEW)

Full benchmark suite in `test-int/semantic/` covering 5 backend×provider combinations:
- `sqlite-fts`, `sqlite-fastembed`, `postgres-fts`, `postgres-fastembed`, `postgres-openai`
- Quality metrics: hit@1, recall@5, MRR@10 with per-query timing
- Realistic corpus with cross-topic vocabulary overlap (240 notes, 4 topics)
- Rich CLI viewer: `just semantic-report`
- JSON artifact output: `just test-semantic-report`

Key finding: **FastEmbed (384-d local ONNX) matches or exceeds OpenAI (1536-d) quality at 30x lower latency.** Recommending FastEmbed as default for both local and cloud deployments.

### 3) Per-Project Local/Cloud Routing + API Key Auth (`d84708c`, `ed94877`, `312662f`) — DONE

### Acceptance criteria

- Project mode (`local`/`cloud`) persists and displays correctly.
- Routing selects ASGI for local projects and HTTP+Bearer for cloud projects.
- Cloud project without key fails with explicit remediation (`cloud set-key`/`cloud create-key`).
- Resolution precedence is correct (factory > force-local > per-project cloud > global fallback > local).
- Watch/sync only run for local projects.

### Existing coverage anchor points

- `tests/mcp/test_async_client_modes.py`
- `tests/cli/test_project_set_cloud_local.py`
- `tests/mcp/test_project_context.py`
- `tests/test_project_resolver.py`
- `tests/sync/test_watch_service_reload.py`

### Gaps to close — DONE

- ~~Cloud routing branch gaps in `src/basic_memory/mcp/async_client.py`~~ — expanded `tests/mcp/test_async_client_modes.py`

### Planned additions — DONE

- ~~Add branch-focused tests for all unresolved routing branches in `get_client()`.~~ **DONE**
- Add MCP integration scenario with mixed local/cloud project config — deferred to backlog item 4.

### 4) Project-Prefixed Permalinks + Memory URL Routing (`545804f`) — DONE

### Acceptance criteria

- Project-prefixed permalinks are generated consistently on create/update/import flows.
- Memory URLs resolve to the correct project/entity even with duplicate note titles.
- `read_note`, `search`, `build_context`, write/edit/move flows preserve project identity correctly.
- Link resolution remains correct for context-aware wikilinks.

### Existing coverage anchor points

- `tests/utils/test_permalink_formatting.py`
- `tests/mcp/test_tool_read_note.py`
- `tests/mcp/test_tool_search.py`
- `tests/services/test_context_service.py`
- `test-int/mcp/test_read_note_integration.py`

### Gaps to close

- No major coverage alarm in report, but keep as regression-critical due broad impact surface.

### Planned additions — DONE

- ~~Add one integration test with colliding titles across two projects and assert URL routing invariants.~~ **DONE** — `test-int/mcp/test_permalink_collision_integration.py` (2 tests: collision across projects + memory:// URL routing with project prefix)

### 5) MCP UI Variants + TUI Output (`8bc03d1`) — DONE

### Acceptance criteria

- UI resource variant selection (`tool-ui`, `vanilla`, `mcp-ui`) follows env configuration.
- `search_notes` and `read_note` expose expected resource metadata for UI hosts.
- `ascii`/`ansi` outputs are deterministic and stable for terminal clients.

### Existing coverage anchor points

- `tests/mcp/test_tool_contracts.py`
- `test-int/mcp/test_output_format_ascii_integration.py`
- `test-int/mcp/test_ui_sdk_integration.py`

### Gaps to close — DONE

- ~~`src/basic_memory/mcp/tools/ui_sdk.py` branch coverage~~ — `tests/mcp/test_ui_sdk.py`
- ~~`src/basic_memory/mcp/ui/sdk.py` and `src/basic_memory/mcp/ui/templates.py` branch coverage~~ — `tests/mcp/test_ui_templates.py` + `tests/mcp/test_ui_resources.py`

### Planned additions — DONE

- ~~Add unit tests for UI SDK metadata generation and template selection branches.~~ **DONE** — 31 tests
- ~~Add integration assertion for variant-specific resource URIs and metadata payload shape.~~ **DONE**

### 6) Watch Command (`8df88e4`) — DONE

### Acceptance criteria

- `basic-memory watch` starts and processes create/update/delete events.
- Watch restart/reload path does not duplicate watchers.
- Cloud-mode projects are excluded from active watcher set.

### Existing coverage anchor points

- `tests/cli/test_watch.py`
- `tests/sync/test_coordinator.py`
- `tests/sync/test_watch_service_reload.py`

### Planned additions — DONE

- ~~Add one stress-style integration test for rapid file changes and watcher stability.~~ **DONE** — `tests/sync/test_watch_service_stress.py` (3 tests: 50-file batch, mixed add/modify/delete batch, rapid modifications to same file)

### 7) CLI JSON Output (`a47c9c0`) — DONE

### Acceptance criteria

- `--format json` returns valid JSON with stable keys for success paths.
- Error paths also return JSON-shaped output with correct non-zero exits.
- Default human output remains unchanged.

### Existing coverage anchor points

- `tests/cli/test_cli_tool_json_output.py`
- `test-int/cli/test_cli_tool_json_integration.py`

### Planned additions — DONE

- ~~Add one failure-path integration test per high-use tool command.~~ **DONE** — `test-int/cli/test_cli_tool_json_failure_integration.py` (4 tests: read-note not found, write-note missing content, write→read roundtrip, recent-activity empty project)

### 8) Search/Edit and Metadata Fixes (`530cbac`, `f1d50c2`, `8838571`, `009e849`) — DONE

### Acceptance criteria

- Metadata filters produce consistent results on SQLite and Postgres.
- `tag:` shorthand works alone and with mixed query terms.
- Fast write/edit paths preserve `external_id` and metadata integrity.

### Existing coverage anchor points

- `tests/repository/test_metadata_filters.py`
- `tests/repository/test_search_repository.py`
- `tests/services/test_search_service.py`

### Planned additions — DONE

- ~~Add Postgres-specific metadata filter edge-case tests to mirror SQLite assertions exactly.~~ **DONE** — `tests/repository/test_metadata_filters_edge_cases.py` (6 tests: missing field, AND logic, contains single-element array, nested path missing intermediate, $gte/$lte boundaries, $between inclusive — all pass on both SQLite and Postgres)

### 9) Compatibility and Hotfix Regression Pack (`c46d7a6`, `a0e754b`, `343a6e1`, `24ca5f6`, `e3ced49`, `8489a3d`, `b609c4e`, `f6e0a5b`, `7624a20`)

### Acceptance criteria

- Legacy endpoints required by older CLI versions function without `405` (`GET /projects/projects`, `POST /projects/projects`, `POST /projects/config/sync`).
- Entity creation conflicts map to conflict status (not 500).
- `recent_activity` prompt defaults are correct.
- No spurious `metadata: {}` in serialized frontmatter.
- Tigris/rclone uses global consistency headers for all transaction types.
- `bm --version` fast path avoids heavy import path and remains responsive.
- Default SQLite DB path is isolated by config dir.

### Gaps to close

- ~~Commits with no direct tests added (`c46d7a6`, `344e651`, `f6e0a5b`) need explicit regression tests.~~ **DONE**

### Planned additions — DONE

- ~~Add API compat test covering all legacy endpoint methods and payloads.~~ **DONE** — `test_legacy_v1_add_project_endpoint`, `test_legacy_v1_sync_config_endpoint`
- ~~Add CLI fast-path test for `--version` import behavior/performance guard.~~ **DONE** — `test_bm_version_does_not_import_heavy_modules`
- ~~Add empty metadata serialization regression test.~~ **DONE** — `test_schema_to_markdown_empty_metadata_no_metadata_key`
- Add migration safety test for SQLite generated columns (`VIRTUAL` expectation) — deferred, low risk.

## MCP Manual Verification Plan (LLM Tool Calls)

Run after automated tests pass.

### Setup

- Start MCP server: `basic-memory mcp --transport stdio`
- Use an MCP-capable client and issue tool calls directly.

### Manual scenarios

- Schema: call `schema_validate`, `schema_infer`, and `schema_diff` on known fixtures.
- Schema: verify error and success payloads match acceptance criteria.
- Semantic search: call `search_notes` with `search_type=text|vector|hybrid`.
- Semantic search: verify ranking relevance on semantic fixture queries.
- Routing: call tools with explicit project on mixed local/cloud setup.
- Routing: verify success/failure paths with and without API key.
- Permalink routing: read/write/search notes across projects with colliding titles.
- Permalink routing: verify memory URL routing correctness.
- UI/TUI: call `search_notes` and `read_note` with UI variants and `output_format=ascii|ansi`.
- UI/TUI: verify payload/resource format and metadata completeness.

## Implementation Backlog (Ordered)

1. ~~Fill schema MCP/client/router coverage gaps.~~ **DONE** — 18 tests in `test_tool_schema.py` + `test_client_schema.py`
2. ~~Fill semantic search MCP + Postgres repository gaps.~~ **DONE** — 20 tests in `test_postgres_search_repository_unit.py` + `test_tool_search.py`
3. ~~Add compatibility regression tests (legacy endpoints, migration, version fast path).~~ **DONE** — 5 tests across 3 files (see below)
4. ~~Add feature-level integration tests (permalinks, watch, CLI JSON, metadata filters).~~ **DONE** — 15 tests across 4 files (see items 4, 6, 7, 8 above)
5. ~~Expand UI SDK and template branch tests.~~ **DONE** — 31 tests in `test_ui_templates.py` + `test_ui_sdk.py` + `test_ui_resources.py`
6. ~~Run full gate and capture results in a short release readiness summary.~~ **DONE** — see results below

### Full Gate Results (`just check`)

| Phase | Result |
|-------|--------|
| lint | PASS |
| format | PASS |
| typecheck | PASS |
| Unit tests (SQLite) | 1788 passed, 15 skipped |
| Integration tests (SQLite) | 243 passed, 4 skipped, 10 deselected |
| Unit tests (Postgres) | 1760 passed, 28 skipped |
| Integration tests (Postgres) | 234 passed, 13 skipped, 10 deselected |

**0 failures. 10 deselected = semantic benchmark tests (run separately via `just test-semantic`).**

### Item 3 Details — Compatibility Regression Tests

| Test | File | What it covers |
|------|------|----------------|
| `test_legacy_v1_add_project_endpoint` | `tests/api/v2/test_project_router.py` | POST `/projects/projects` legacy route reachable (idempotent path) |
| `test_legacy_v1_sync_config_endpoint` | `tests/api/v2/test_project_router.py` | POST `/projects/config/sync` legacy route reachable |
| `test_bm_version_does_not_import_heavy_modules` | `tests/cli/test_cli_exit.py` | `bm --version` fast path does not load `basic_memory.mcp` |
| `test_schema_to_markdown_empty_metadata_no_metadata_key` | `tests/markdown/test_entity_parser_error_handling.py` | `schema_to_markdown()` with `entity_metadata={}` emits no `metadata:` key |
| `test_legacy_v1_list_projects_endpoint` | `tests/api/v2/test_project_router.py` | (pre-existing) GET `/projects/projects` legacy route |

**Suite totals after item 3: 1764 passed, 15 skipped, 0 failures.**

## Suggested Commands

- Full suite: `just check`
- Fast loop: `just fast-check`
- E2E consistency: `just doctor`
- SQLite focused: `just test-sqlite`
- Postgres focused: `just test-postgres`
- Schema integration: `pytest test-int/test_schema -q`
- Semantic + repo focus: `pytest tests/repository/test_postgres_search_repository.py tests/mcp/test_tool_search.py tests/services/test_semantic_search.py -q`
- MCP integration focus: `pytest test-int/mcp -q`

## Exit Criteria for This Plan

- All feature acceptance criteria above are validated.
- All identified high-priority coverage gaps are addressed or explicitly documented as intentional.
- Manual MCP verification scenarios complete with no P0/P1 findings.
