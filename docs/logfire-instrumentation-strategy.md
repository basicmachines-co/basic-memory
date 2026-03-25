# Logfire Instrumentation Strategy

## Why

We want Logfire in Basic Memory for two specific use cases:

1. Local development and performance investigation
2. Cloud deployments where Basic Memory runs inside Basic Memory Cloud

This instrumentation must be:

- Disabled by default
- Useful when enabled
- Safe for local-first users
- Searchable in Logfire over time

The previous integration added telemetry, but it leaned too much on generic framework instrumentation. That created noisy spans with weak names and made the trace view harder to navigate. This strategy favors manual instrumentation around Basic Memory's real units of work.

## Core Principles

### 1. Default-off

Basic Memory should ship with Logfire disabled unless the operator explicitly enables it.

That means:

- no required token for normal local usage
- no surprise outbound telemetry
- no behavior change for existing users

### 2. Manual spans over automatic framework spans

We should not rely on broad auto-instrumentation for FastAPI, MCP, SQLAlchemy, or HTTP as the primary experience.

Why:

- auto-generated span names are often generic
- routes and middleware produce too many low-signal spans
- it becomes harder to answer product questions like "why was `write_note` slow?" or "where did sync time go?"

The preferred model is:

- one meaningful root span per high-level operation
- a small number of child spans for important phases
- optional targeted instrumentation only where it adds clear value

### 3. Logs must live inside traces

Basic Memory already uses `loguru` pervasively. The Logfire integration should preserve that and make those logs visible inside the active trace/span context.

If traces exist but the logs are detached from them, the integration is not doing its job.

### 4. Stable names, selective attributes

Span names should describe the operation class, not the specific input.

Good:

- `mcp.tool.write_note`
- `sync.project.scan`
- `search.execute`
- `routing.resolve_project`

Bad:

- `Searching for "foo bar baz"`
- `POST /v2/projects/123/search/`
- `write note to /specs/api.md`

Dynamic values belong in attributes, not in the span name.

## What We Should Not Do

### Avoid broad FastAPI auto-instrumentation

We should not turn on `instrument_fastapi()` and treat that as the main telemetry story.

It may still be useful in narrowly scoped debugging, but it should not define the production trace shape. The meaningful root spans should come from Basic Memory's own entrypoints and service boundaries.

### Avoid per-file spans by default

`sync` can process many files. A span per file will explode trace cardinality and make performance views noisy.

Default behavior should be:

- one span for the project sync
- child spans for scan, move handling, delete handling, markdown sync batch, relation resolution, embedding sync, watermark update
- per-file spans only for failures or very slow outliers

### Avoid high-cardinality attributes on every span

Do not attach large or highly variable values everywhere:

- raw note content
- file bodies
- long search text
- arbitrary metadata blobs
- unique IDs that make every span shape distinct

Prefer compact, queryable attributes:

- `project_name`
- `workspace_id`
- `route_mode`
- `scan_type`
- `file_count`
- `result_count`
- `search_type`
- `retrieval_mode`
- `duration_ms`

## Proposed Architecture

Add a dedicated telemetry module in core Basic Memory, separate from logging setup.

Suggested shape:

```python
# basic_memory/telemetry.py

def configure_telemetry(service_name: str, *, enable_logfire: bool) -> None: ...
def telemetry_enabled() -> bool: ...
def span(name: str, **attrs): ...
def bind_telemetry_context(**attrs): ...
```

This module should:

- configure Logfire only when explicitly enabled
- set up the Logfire `loguru` handler
- expose lightweight helpers so application code does not import `logfire` directly everywhere
- degrade cleanly to no-op behavior when disabled

This keeps the rest of the codebase readable and makes it easy to reason about what telemetry is doing.

## Logging Integration Strategy

### Goal

When a span is active, logs emitted through `loguru` during that operation should show up in the same trace.

### Preferred design

1. Configure Logfire once in the telemetry bootstrap
2. Add the Logfire `loguru` handler to the existing `loguru` configuration
3. At operation boundaries, bind stable contextual fields with `loguru`
4. Let logs emitted inside the span inherit the active trace context

### Context to bind

Bind only the fields that help correlate work across the system:

- `service_name`
- `entrypoint`
- `project_name`
- `workspace_id`
- `route_mode`
- `tool_name`
- `command_name`

This binding should happen at the root of an operation, not deep in leaf functions.

### Important nuance

We should not try to encode the entire trace model into logger extras. The logger context should be a human-meaningful slice of the active operation. Trace linkage comes from the active Logfire/OpenTelemetry context; logger extras are there to improve searchability and readability.

## Span Model

### Root spans

Each user-visible or system-visible operation should get one root span.

Examples:

- `cli.command.status`
- `cli.command.project_sync`
- `api.request.search`
- `mcp.tool.write_note`
- `mcp.tool.read_note`
- `mcp.tool.search_notes`
- `sync.project.run`
- `db.semantic_backfill`

### Child spans

Child spans should represent real phases whose duration we care about.

Examples:

- `routing.client_session`
- `routing.resolve_project`
- `routing.resolve_workspace`
- `api.search.execute`
- `sync.project.scan`
- `sync.project.detect_moves`
- `sync.project.apply_changes`
- `sync.project.resolve_relations`
- `sync.project.sync_embeddings`
- `sync.file.markdown`
- `sync.file.regular`
- `search.execute`
- `search.relaxed_fts_retry`
- `db.init`
- `db.migrate`

### Span naming rules

- Use dot-separated names
- Start with subsystem
- Keep the verb at the end
- Keep names stable across runs
- Never include request-specific text in the span name

## Attribute Taxonomy

### Required attributes on root spans

Every root span should have a small common set:

- `service_name`
- `entrypoint`
- `project_name` when applicable
- `workspace_id` when applicable
- `route_mode` with values like `local_asgi`, `cloud_proxy`, `factory`

### Operation-specific attributes

Examples:

For search:

- `search_type`
- `retrieval_mode`
- `page`
- `page_size`
- `result_count`
- `fallback_used`

For sync:

- `scan_type`
- `force_full`
- `new_count`
- `modified_count`
- `deleted_count`
- `move_count`
- `skipped_count`
- `embeddings_enabled`

For note operations:

- `tool_name`
- `note_type`
- `directory`
- `overwrite`
- `output_format`

### Attributes to avoid by default

- full `query.text`
- full note titles if they create privacy or cardinality issues
- file content
- raw frontmatter
- raw HTTP bodies

If we need richer payloads for a local debugging session, that should be an explicit temporary mode, not the default telemetry shape.

## Instrumentation Plan By Layer

### 1. Entrypoints

Instrument these first:

- `cli.app` callback and major commands
- API lifespan and selected routers
- MCP server lifespan
- MCP tool entrypoints

Why:

- this establishes clean root spans
- it gives us trace boundaries that match how users think about the product

### 2. Routing and context resolution

Instrument:

- client routing decisions
- workspace resolution
- project resolution
- default-project fallback

Why:

- Basic Memory has local/cloud/per-project routing logic
- when something is slow or surprising, we need to know which path was taken

### 3. Sync and indexing

This is the highest-value area to instrument deeply.

Instrument:

- sync root
- scan strategy decision
- filesystem scan
- move detection
- delete handling
- markdown sync phase
- relation resolution
- vector embedding sync
- scan watermark update

Why:

- this is where performance work will happen
- cloud and local both benefit from this visibility

### 4. Search

Instrument:

- search execution
- retrieval mode
- relaxed FTS fallback
- result shaping

Why:

- search is user-facing and latency-sensitive
- hybrid/vector/FTS paths need to be distinguishable

### 5. Database and initialization

Instrument selectively:

- DB init
- migrations
- semantic backfill
- connection mode selection

Avoid full automatic SQL span firehose by default.

## Recommended Rollout Phases

## Task List

- [x] Phase 1: Bootstrap and config gating
- [x] Phase 2: Root spans for entrypoints and primary operations
- [x] Phase 3: Child spans for sync, search, and routing
- [x] Phase 4: Failure-focused detail and final verification
- [x] Phase 5: Loguru context binding and scoped context inheritance

## Recommended Rollout Phases

### Phase 1: Bootstrap and config gating

Add:

- telemetry bootstrap module
- config/env gating
- `loguru` + Logfire handler integration

This gives immediate value with low noise.

### Phase 2: Root spans for entrypoints and primary operations

Add:

- root spans for CLI, API, MCP, and main MCP tools
- stable root attributes for project, workspace, route mode, and operation type

This gives us clean top-level traces that match how users think about the product.

### Phase 3: Child spans for sync, search, and routing

Add child spans to:

- sync
- search
- routing

This is the main performance-investigation layer.

### Phase 4: Failure-focused detail

Add selective deeper spans/log enrichment for:

- sync failures
- relation resolution failures
- slow file operations
- cloud routing/auth failures

This keeps normal traces clean while improving debuggability.

### Phase 5: Loguru context binding and scoped context inheritance

Add:

- context-local telemetry state in `basic_memory.telemetry`
- a shared `scope(...)` helper that opens a span and binds stable logger context together
- context inheritance for routing, sync, and search so downstream `loguru` logs carry the active operation fields

This makes the trace view and the log stream tell the same story without forcing logger rewrites across the codebase.

## Local Dev Playbook

The fastest way to sanity-check the current trace shape is:

```bash
LOGFIRE_TOKEN=lf_... just telemetry-smoke
```

What this does:

- creates an isolated temp home, config dir, and project path
- enables Logfire for the run
- automatically exports to Logfire when `LOGFIRE_TOKEN` is present
- defaults `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false` so the smoke run stays fast and trace-friendly
- disables promo telemetry so the trace is about Basic Memory work, not analytics noise
- runs a small CLI workflow:
  - `project add`
  - `tool write-note`
  - `tool read-note`
  - `tool edit-note`
  - `tool build-context`
  - `tool search-notes`
  - `doctor`

If you want to exercise the instrumentation without exporting anything upstream:

```bash
BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE=false just telemetry-smoke
```

If you want the smoke run to include vector or hybrid retrieval spans too:

```bash
LOGFIRE_TOKEN=lf_... BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true just telemetry-smoke
```

The recipe sets `BASIC_MEMORY_LOGFIRE_ENVIRONMENT=telemetry-smoke` by default so these traces are easy to isolate in Logfire. Override it if you want the smoke traces grouped under a different environment name.

### What to look for

You should see a small set of comparable root spans rather than a framework-generated span forest:

- `cli.command.project`
- `cli.command.tool`
- `mcp.tool.write_note`
- `mcp.tool.read_note`
- `mcp.tool.edit_note`
- `mcp.tool.build_context`
- `mcp.tool.search_notes`
- `sync.project.run`

You should also see correlated logs under those traces with stable fields like:

- `project_name`
- `route_mode`
- `tool_name`
- `entrypoint`

### Expected nuance

`doctor` creates its own temporary project on purpose. That means the sync trace will usually show a different project name than the `telemetry-smoke` write/search traces. That is fine for smoke testing because the goal is to confirm:

- root span names are meaningful
- scoped logs stay attached to the active trace
- routing, tool, search, and sync phases are easy to distinguish

## Validation Checklist

We should consider the integration successful when the following are true:

1. With telemetry disabled, Basic Memory behaves exactly as it does today.
2. With telemetry enabled, one user action produces one obvious root span.
3. Logs emitted during that action are visible inside the same trace.
4. A search in Logfire for `mcp.tool.write_note` or `sync.project.run` returns comparable spans across runs.
5. Trace views show phase timing clearly without drowning in framework noise.
6. Sensitive payloads are not captured by default.

## Immediate Implementation Direction

When we start coding, the first pass should be:

1. Add `basic_memory.telemetry`
2. Add config/env switches for `enabled`, `send_to_logfire`, and service name
3. Wire telemetry bootstrap into CLI, API, and MCP entrypoints
4. Configure `loguru` to emit to both existing sinks and the Logfire handler when enabled
5. Add manual root spans around:
   - CLI commands
   - API request handlers we care about
   - MCP tool entrypoints
   - sync root
   - search root
6. Add child spans to the sync and routing phases only after the root span model feels clean

That gives us a strong foundation without repeating the earlier "turn on instrumentation everywhere" approach.
