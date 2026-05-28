# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] — 2026-05-23

### Fixed
- **Let Basic Memory v0.21.3 self-route workspace-qualified identifiers and URLs.** Hermes no longer injects its configured default project into `bm_read`, `bm_edit`, `bm_delete`, `bm_move`, or `bm_context` calls when the identifier/URL is already workspace-qualified, such as `personal/main/...`, `memory://personal/main/...`, or an organization workspace slug with a 32-character hash suffix. This preserves Basic Memory Cloud's workspace-aware routing while keeping the existing default-project behavior for short/local identifiers.

## [0.3.1] — 2026-05-16

### Changed
- **Documented the Hermes Agent v0.14.0-compatible `/bm-*` slash-command monkeypatch.** `MONKEYPATCH.md` now distinguishes the plugin's runtime version from the Hermes Agent-side compatibility patch: plugin `v0.3.0` remains the correct runtime release for Hermes Agent `v0.13.x`, while Hermes Agent `v0.14.0` still needs the updated two-part core patch so gateway startup command discovery loads the active exclusive memory provider and the memory-provider collector delegates `register_command` / `register_skill`.
- **Clarified install guidance for users and agents.** The README known-issue section now points Hermes `v0.13.x` and `v0.14.0` users at the compatibility matrix in `MONKEYPATCH.md`, so agents do not mistake a plugin update for the required Hermes Agent core patch.

### Notes
- This is a documentation/compatibility-instructions release only. It does not change the plugin runtime code or Basic Memory data behavior. The plugin remains backward-compatible with Hermes Agent `v0.13.x`; the new documentation explains how to patch Hermes Agent `v0.14.0` until the upstream Hermes fix ships.

## [0.3.0] — 2026-05-12

### Added
- **Per-call project routing on every `bm_*` tool.** All eight tools now accept optional `project` (name) and `project_id` (UUID from `bm_projects`) parameters. The agent can write or read against a project other than the Hermes-configured one — useful when the user asks to write into a different cloud project (e.g. a personal `main` project) without reconfiguring the plugin. `project_id` takes precedence over `project`; both fall back to the configured default when omitted. Workspace routing is handled transparently by BM via `project_id` — no separate workspace parameter is needed.
- **`bm_projects` and `bm_workspaces` agent tools.** Promotes the discovery logic previously available only as `/bm-project` and `/bm-workspace` slash commands to agent-facing tools. `bm_projects` returns JSON with `name` and `external_id` (UUID) per project so the agent can hand the UUID to `bm_write` / `bm_read` / etc. via `project_id` — the unambiguous form across cloud workspaces. `bm_workspaces` lists BM Cloud workspaces (name, type, role, default flag). Together with per-call routing, these unblock the workflow Drew's friction note flagged: agent picks the right project + workspace before writing, instead of silently operating against the active Hermes memory project.
- **SKILL.md cross-project workflow** documenting the discovery → route → write → verify recipe end-to-end. Adds a "Permalinks" section covering the three canonical shapes (short, project-qualified, workspace-qualified) and the round-trip property where `bm_write`'s returned permalink self-routes for follow-up reads. A "Cross-project routing" section explains `project` (including workspace-qualified syntax like `"personal/main"`) vs `project_id` and when to use each. Also backfills `bm_recent` documentation (the tool shipped in 0.2.0 but the skill hadn't been updated).
- **SKILL.md "Further reading" section** linking to the official docs at [docs.basicmemory.com](https://docs.basicmemory.com), with raw-markdown URLs (`/raw/<path>.md`) the agent can `WebFetch` on demand for deeper material — knowledge format, observations & relations, memory URL wildcards, semantic search, cloud routing, BM's full MCP tool surface, and the `llms.txt` sitemap.

### Notes
- Addresses the routing, discovery, and documentation gaps in the real-world note "Hermes Basic Memory Cloud Task Experience." A proposed `bm_import` tool was evaluated and dropped — `read_file` + `bm_write` already composes the same operation with no new capability, at the cost of one more tool in the surface.
- The slash commands `/bm-project` and `/bm-workspace` still exist and behave identically — they continue to call `list_memory_projects` / `list_workspaces` directly via the actor. No behavior change for human use.

## [0.2.0] — 2026-05-11

### Added
- **Plugin-owned `/bm-*` slash commands** for CLI/gateway sessions. Eight commands give humans direct memory-graph access without going through the agent: `/bm-search`, `/bm-read`, `/bm-context`, `/bm-recent`, `/bm-status`, `/bm-remember`, `/bm-project`, `/bm-workspace`. Closes #2.
- **`bm_recent` tool** wrapping BM's `recent_activity`. Surfaces notes updated within a timeframe (`7d` default, accepts natural language like `"2 weeks"` or `"yesterday"`). Agent-facing and reused by `/bm-recent`.
- **`remember_folder` config key** (default `"bm-remember"`). Separate from `capture_folder` so manual captures via `/bm-remember` don't intermix with auto-generated session transcripts. Notes are tagged `manual-capture` for further disambiguation.

### Fixed
- **`ctx.register_skill(...)` was silently no-opping since 0.1.5** in real Hermes installs. Hermes loads memory-provider plugins through a stripped-down `_ProviderCollector` context (`plugins/memory/__init__.py`) that captures only `register_memory_provider`; `register_skill` and `register_command` are not delegated. The plugin now writes directly to `PluginManager._plugin_commands` and `_plugin_skills`, matching the entry shape and name normalization `PluginContext.register_command` / `register_skill` produce. This makes both the new slash commands and the bundled SKILL.md work in current Hermes installs. The clean fix lives upstream — a small patch to teach `_ProviderCollector` to delegate — and once that lands, the reach-in becomes a redundant double-write of identical entries. Forward-compat `ctx.register_command` / `ctx.register_skill` calls remain in place for the future code path.

### Notes
- `/bm-remember` derives the title from the first non-empty line of the input, trimmed to 80 chars; falls back to `Note YYYY-MM-DD HHMM UTC`.
- `/bm-workspace` short-circuits in local mode with a one-line explanation. Workspaces are a BM Cloud concept.
- Mid-session project/workspace switching is intentionally not supported in 0.2.0 — auto-capture would land in unexpected places. Tracked as a follow-up.

## [0.1.7] — 2026-05-10

### Changed
- **Stronger nudge in `system_prompt_block()`** to steer agents toward the `bm_*` tools instead of shelling out to `bm` CLI. Pre-v0.1.7 the prompt listed the tools neutrally; given Claude/Hermes models' heavy training-data exposure to `bm tool ...` CLI patterns, neutral language wasn't enough — agents reached for the shell by reflex, paying 1-2s of cold-start per call instead of ~0.1s through our persistent MCP connection. New prompt is explicit (**"Use the `bm_*` tools below directly — do not shell out to the `bm` CLI"**) and gives a one-line latency rationale so the model has a reason to follow it.
- `SKILL.md` mirrors the directive with a "Use `bm_*`, not the `bm` CLI" section + a tool-vs-CLI table.

### Added
- Regression test `test_system_prompt_block_steers_away_from_cli` locks in the directive language so future prompt edits don't accidentally weaken it.

## [0.1.6] — 2026-05-10

### Fixed
- **`bm_*` tools were never registered with Hermes's `MemoryManager._tool_to_provider`.** `get_tool_schemas()` was gated on `self._initialized`, but Hermes captures the schema list at *register* time — before `initialize()` runs. The gate caused every session to start with zero tools registered for our provider, so every LLM-issued `bm_search` (and friends) returned `"Unknown tool: bm_search"` from MemoryManager's dispatch. Symptoms were asymmetric: prefetch (recall injection) worked because it's invoked per-turn after init, but tool calls didn't. Schemas are static — they now return unconditionally, with `handle_tool_call()` doing the runtime "is the actor ready?" gate.
- Regression test pins this so we don't reintroduce it: `test_get_tool_schemas_unconditional` asserts `get_tool_schemas()` returns all 7 schemas on a fresh, uninitialized provider.

## [0.1.5] — 2026-05-10

### Added
- Bundled `SKILL.md` is now auto-registered via `ctx.register_skill("basic-memory", ...)` during plugin load. No more manual symlink to `~/.hermes/skills/`. The skill is opt-in (resolvable via `skill:view basic-memory:basic-memory`); always-on agent guidance still flows through `system_prompt_block()`.

### Changed
- README rewritten for community install. Lead command is now `hermes plugins install basicmachines-co/hermes-basic-memory`. Clone-and-symlink instructions moved to the Development section.
- Added GitHub Actions CI: unit tests on push and PR.
- Added this CHANGELOG.

## [0.1.4] — 2026-05-10

### Added
- `_uv_binary_path()` and `_install_bm_via_uv()`. When `bm` is missing from the host, the plugin runs `uv tool install basic-memory --quiet` once at first `initialize()`. The bm binary lands at `~/.local/bin/bm` — the same canonical path a manual `uv tool install basic-memory` produces, so subsequent manual installs are no-ops rather than creating a second install.
- 8 new unit tests covering `is_available()` with bm/uv combinations, the install subprocess (success / non-zero exit / OSError / no-uv), and `initialize()` install-or-not branching.

### Changed
- `is_available()` now returns `True` when **either** `bm` is on disk **or** `uv` is on disk (we can install the missing CLI ourselves).
- README's prerequisites section: dropped manual basic-memory install requirement; added the one-time ~10s cold-start note.

## [0.1.3] — 2026-05-10

### Fixed
- README's cloud-mode section described the wrong setup (`bm project add ... --cloud --local-path` + `bm cloud bisync`), which gives a local-mode project with file-level cloud sync rather than true cloud routing. Replaced with `bm project set-cloud <name> --workspace <name>`, which flips the project to `ProjectMode.CLOUD` so tool calls route over HTTPS to `<cloud_host>/proxy` directly. No local files involved.
- Documented OAuth / API-key auth options, and the `--workspace` requirement when the user belongs to multiple BM Cloud workspaces.

## [0.1.2] — 2026-05-10

### Changed
- `_default_project()`: `"hermes-memory"` (was `"hermes-{hostname}"`).
- `_default_project_path()`: `~/hermes-memory/` (was `~/.basic-memory/hermes/`). The previous path violated the principle that `~/.basic-memory/` is reserved for BM's app state, not project storage.

### Added
- `_bm_known_projects()` reads bm's `~/.basic-memory/config.json`. `BasicMemoryProvider._verify_project_registered()` uses it to refuse initialization when `mode: cloud` is set against a project that isn't registered with bm. Local mode still auto-creates as before.
- 13 new unit tests for the introspection + bail-out paths.

## [0.1.1] — 2026-05-10

### Added
- `tests/test_actor.py` — 15 tests covering `_BmMcpActor` lifecycle, call dispatch, timeout-with-cancellation, idempotent shutdown.
- `tests/test_capture.py` — 25 tests for `sync_turn` (first-write + append paths), `on_session_end` summary shape, and gating.
- `tests/test_prefetch.py` — 25 tests for `prefetch` / `queue_prefetch` / `_format_prefetch` including forward-compat with unknown response fields.
- `tests/test_integration.py` — 12 gated tests exercising every tool against a real `bm` MCP server (`BM_INTEGRATION=1` + `bm` + `mcp`). Each session uses a throwaway BM project that's torn down on completion.

### Changed
- `_BmMcpActor.call` now refuses calls after `shutdown()` (sets `_running=False`) and cancels the underlying coroutine on timeout instead of leaking it.
- `_format_prefetch` defensively coerces non-string fields and skips non-dict entries.
- Added module-level `__version__`, kept in sync with `plugin.yaml` (verified by a test).

## [0.1.0] — 2026-05-10

### Added
- Initial release of the Hermes Memory Provider plugin for Basic Memory.
- Seven `bm_*` agent tools: `bm_search`, `bm_read`, `bm_write`, `bm_edit`, `bm_context`, `bm_delete`, `bm_move`.
- Per-turn capture (`sync_turn`) and end-of-session summary (`on_session_end`).
- Local mode (default) with auto-created BM project; cloud mode with project-name-based routing.
- Single-file plugin at `__init__.py`, AGPL-3.0-or-later.
- 84-test pytest suite.

[0.3.2]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.3.2
[0.3.1]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.3.1
[0.3.0]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.3.0
[0.2.0]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.2.0
[0.1.7]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.7
[0.1.6]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.6
[0.1.5]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.5
[0.1.4]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.4
[0.1.3]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.3
[0.1.2]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.2
[0.1.1]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.1
[0.1.0]: https://github.com/basicmachines-co/hermes-basic-memory/releases/tag/v0.1.0
