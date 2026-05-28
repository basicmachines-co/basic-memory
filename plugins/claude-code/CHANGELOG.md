# Changelog

## Unreleased — v0.4 bridge redesign (Phase 1)

The plugin is reframed as the **bridge between Claude's working memory and Basic
Memory's durable graph**, rather than a memory layer of its own. See
[DESIGN.md](./DESIGN.md) for the full rationale and roadmap.

### Added

- **SessionStart hook** (`hooks/session-start.sh`) — briefs Claude at session
  start with active tasks from the graph (one structured `type: task` query) plus
  an always-on recall prompt. Works against the default project with zero config;
  pin a project via `basicMemory.primaryProject`. Plain-stdout output, capped well
  under the 10k limit, and silent if Basic Memory isn't installed.
- **PreCompact hook** (`hooks/pre-compact.sh`) — writes a `type: session`
  checkpoint to the graph before context compaction (extractive in this phase;
  LLM-summarized capture is the next step). Only writes when a `primaryProject` is
  configured, so it never touches a graph the user hasn't opted in.
- **Output style** (`output-styles/basic-memory.md`) — opt-in reflexes: search
  before recalling, capture decisions as typed `decision` notes, cite permalinks.
  Sets `keep-coding-instructions: true` so it composes with normal dev work.
- **Seed schemas** (`schemas/{session,decision,task}.md`) — picoschema for the
  note types the plugin writes, so recall via `search_notes` metadata filters is
  precise. `task` mirrors the framework-agnostic `memory-tasks` skill. Validation
  mode `warn` — advisory, never blocking.
- **`settings.example.json`** — copyable configuration with sensible defaults.

### Removed (clean break)

- The six bundled skills (`placement`, `knowledge-capture`, `knowledge-organize`,
  `continue-conversation`, `research`, `edit-note`). Equivalent, framework-agnostic
  workflows live in the top-level [`skills/`](../../skills) package
  (`memory-notes`, `memory-research`, `memory-tasks`, `memory-schema`, …); install
  those for the old capabilities.
- The `basic-memory-manager` agent. The plugin ships no agent in v0.4 — memory is
  handled in the main context via hooks and the output style, not delegated.
- The `PreToolUse`/`PostToolUse` `write_note` hooks (placement advisory + save
  confirmation). Placement guidance now lives in the `basicMemory` settings block
  and the output style.
- The `basic-memory` config-note convention, superseded by `.claude/settings.json`.
- `PLUGIN.md`, replaced by a bridge-framed `README.md` and `DESIGN.md`.

### Notes

- Slash commands shipped by later phases (`/basic-memory:setup`,
  `:remember`, `:status`) will be **plugin-namespaced** — Claude Code namespaces
  all plugin skills as `/<plugin>:<skill>`.
- Requires `basic-memory >= 0.19.0` (for `metadata_filters` / structured recall).

## 0.3.13

### Fixed

- **`knowledge-capture` thread lookup uses `metadata_filters` instead of `query`.** The previous instructions told the skill to find an existing thread note via `search_notes(query="<uuid>")` — a full-text search. In practice this doesn't find the note even though `thread_id` is in the frontmatter: full-text indexing apparently doesn't surface YAML frontmatter custom fields, or scores those matches too low.

  The reliable lookup is `search_notes(metadata_filters={"thread_id": "<uuid>"})` — direct query against the metadata field. This consistently returns the matching note (with score 0.0 — direct match) when one exists.

- **Skill instructions now show `overwrite=True`** for the update path, since `write_note` requires it to replace an existing note at the same path.

### Discovery

This bug was found by exercising the skill in real-world conditions: invoking `/knowledge-capture` a second time in the same thread, expecting the existing note to be found and rewritten. Search returned weak unrelated matches; the existing note was missed; the update flow couldn't proceed without manual intervention.

## 0.3.12

### Fixed

- **`knowledge-capture` session UUID detection.** The previous bash command used `pwd` to scope the JSONL lookup to a single project directory:
  ```bash
  ls -t ~/.claude/projects/$(pwd | sed 's:/:-:g')/*.jsonl | head -1 | ...
  ```
  This fails when the shell has `cd`'d into a subdirectory of the Claude Code session's project root — the encoded path no longer matches a real `~/.claude/projects/<project>` directory, and the lookup returns no matches. The skill then can't determine the session UUID and falls back to creating a new note instead of finding/updating an existing one.

  The fix is a cross-project glob: `ls -t ~/.claude/projects/*/*.jsonl` — picks the most recently-modified jsonl across all project dirs. The active session is continuously appending to its jsonl, so it's reliably the most-recent.

## 0.3.11

### Changed

- **`knowledge-capture` skill rewritten** with new semantics:
  - **Purpose** clarified: capture the meaningful context of a Claude Code thread as a single coherent note about where the thread has landed — not a running log.
  - **Same-thread detection.** The skill now derives a stable session UUID from the JSONL transcript filename (`~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`) and stores it as `thread_id` in the note's frontmatter. Subsequent invocations in the same thread find and **rewrite** the existing note rather than creating a new one.
  - **Synthesis, not append.** When updating, the skill produces a fresh coherent version that integrates the latest understanding. Superseded decisions are replaced inline; brief prose acknowledges material revisions where relevant. There is no appended changelog at the bottom of the note.
  - **Escape hatch.** If the user explicitly requests a separate note, the skill skips the same-thread lookup and creates a fresh note without a `thread_id`.
  - **Examples updated** with preceding-conversation context, showing both a first capture and a subsequent update in the same thread (a brand design conversation that revises colors and fonts).

## 0.3.10

### Removed

- **All slash commands** (`/remember`, `/organize`, `/research`) and the entire `commands/` directory.

### Why

Claude Code auto-exposes every skill as a slash command (e.g., `/knowledge-organize`, `/research`). The plugin's explicit commands duplicated their corresponding skills, producing two entries for the same workflow in the slash-command picker. The skills are the richer primitive; the commands were thin wrappers.

This drops three files and ~200 lines of duplicate content. The Basic Memory MCP server still provides `continue_conversation`, `recent_activity`, `search`, and `ai_assistant_guide` as native slash commands.

### Updated

- `marketplace.json`, `plugin.json`, `README.md`, `PLUGIN.md` — descriptions and structure references updated to reflect skills+hooks (no commands).

## 0.3.9

### Removed

- **`/context` slash command** — collided with Claude Code's built-in `/context` (which shows context window usage). The plugin command was a thin wrapper around the MCP `build_context` tool; users can invoke that tool conversationally with the same effect.

## 0.3.8

### Removed

- **`/continue` slash command** — duplicates the Basic Memory MCP server's `continue_conversation` prompt. Use that instead.
- **`/recent` slash command** — duplicates the Basic Memory MCP server's `recent_activity` prompt. Use that instead.

### Why

The MCP server exposes prompts that surface as slash commands in Claude clients. The plugin's `/continue` and `/recent` were independent implementations of the same workflows, doubling user-visible commands and creating confusion about which one to use. The MCP versions are upstream-maintained and authoritative.

The plugin still ships unique commands that don't have MCP equivalents: `/remember`, `/context`, `/organize`, `/research`.

## 0.3.7

### Removed

- **`spec-driven-development` skill**. The skill encoded one team's internal spec workflow (specific SPEC-N numbering, "Why/What/How/How to Evaluate" structure, a `specs` project assumption). It's not generally useful for other users, and the surrounding plugin already covers the building blocks (notes, observations, relations) that anyone could use to implement their own spec workflow without skill-level guidance.

### Migration

If you relied on the skill, the underlying capabilities are still available via:
- `knowledge-capture` for writing structured notes
- `edit-note` for updating progress
- `continue-conversation` for resuming spec implementations

You can also keep your own copy of the skill locally — it just won't ship with the plugin.

## 0.3.6

### Changed

- **`continue-conversation` skill** — removed the hardcoded "common projects" list (`basic-memory-llc`, `getting-started`, etc.) which were author-specific names with no meaning to new users. The skill now points to `list_memory_projects()` for discovery and `~/.basic-memory/basic-memory.md` for routing rules.
- **`spec-driven-development` skill** — removed the "Using with Slash Commands" section referencing a `/spec` command. That command does not exist in this plugin; documenting it was misleading.

## 0.3.5

### Fixed

- **PreToolUse hook switched from `type: "prompt"` to `type: "command"`.** The prompt-type variant has no documented decision semantics for PreToolUse — the model's response is not parsed for an explicit allow/block decision, so the tool call was blocked even when the model said "proceed." The command-type variant outputs JSON with `permissionDecision: "allow"` and an `additionalContext` reminder, making the hook advisory rather than gate-keeping. The placement skill still runs; it just doesn't gate the write call any more.

### Why

The previous approach treated the hook as a binding gate on the tool call. In practice, that meant any model response shy of an explicit approval keyword was treated as a block — and there's no documented format for what such an approval looks like in a prompt-type PreToolUse hook. Switching to command-type with a known-good JSON shape removes the ambiguity.

The cost: the hook can no longer enforce that placement was considered. The model could in theory ignore the reminder. In practice, the `additionalContext` injection is enough nudge — the placement skill runs reliably in real-world conversations.

## 0.3.4

### Fixed

- **PreToolUse hook re-fired on every `write_note` retry** — the original prompt unconditionally demanded the placement skill be invoked, so when a write retry came after a user confirmation, the model interpreted the hook as a fresh demand and re-ran the skill (asking the user again). The prompt now allows "placement already settled for this write" as a valid state.
- **Placement skill asked too eagerly when there was clear precedent.** Previously, "no topic-matching folder" was treated as ambiguity → ask. Now the skill follows precedent (similar notes already at root or a folder) without asking, even when no folder is a perfect topic match. The skill only asks when there's no config rule, no topic match, *and* no precedent.

## 0.3.3

### Removed

- **Stop hook** that suggested `/remember` at conversation end. The hook entered an infinite re-entry loop in real-world testing: when the model finished its turn awaiting user input, the Stop hook fired with a "consider suggesting /remember" prompt; the model evaluated and decided no action was needed; the model tried to stop again; the hook fired again; and so on, with the user effectively unable to take their turn until interrupting manually.

### Why

The Stop hook predates 0.3.0 and was not modified by the placement work, but the v0.3.2 smoke test made the loop visible (since 0.3.2 was the first release whose hooks actually fired for the test environment — see 0.3.2 notes). Rather than ship a broken hook, removing it is the right call. Users can still invoke `/remember` manually.

A guarded re-entry-safe version may return in a later release once the Claude Code Stop-hook semantics (`stop_hook_active` and similar) are understood and can be wired in safely.

## 0.3.2

### Fixed

- **Hook matchers now use regex** — `mcp__.*__write_note` instead of the literal `mcp__basic-memory__write_note`. The previous matcher only fired for users running a locally-installed Basic Memory MCP server. Users on the claude.ai Basic Memory Cloud connector (or any other MCP server name) had hooks that silently never fired. The regex catches all variants.

### Changed

- Hook prompt body and skill/PLUGIN.md descriptions updated to be tool-name-agnostic, matching the new matcher.

### Known limitations

- Slash commands (`/remember`, `/research`, etc.) and the `basic-memory-manager` agent still have `allowed-tools` frontmatter that lists exact tool names (`mcp__basic-memory__*`). Users on alternative MCP server names may find these commands have no tool access. Pattern support in `allowed-tools` is being investigated for a follow-up release.

## 0.3.1

### Changed

- **Marketplace renamed** from `basicmachines` to `basicmachines-co` so the install identifier matches the GitHub org slug. Install command is now `/plugin install basic-memory@basicmachines-co` (was `basic-memory@basicmachines`).

### Migration

If you installed the plugin before 0.3.1:
1. Remove the old reference from `.claude/settings.json` (`basic-memory@basicmachines` in `enabledPlugins` or `installed`).
2. Re-install with the new identifier: `/plugin install basic-memory@basicmachines-co`.

The `extraKnownMarketplaces` block (if you used one) also needs the key updated from `"basicmachines"` to `"basicmachines-co"`.

## 0.3.0

### Added

- **`placement` skill** — decides which folder a new note belongs in. Runs automatically before every `mcp__basic-memory__write_note` call via a `PreToolUse` hook. Reads project conventions from a unified config file and applies a short-circuit decision flow (config → tree → search → ask).
- **Unified config file** (`basic-memory.md`) — a single config surface for project conventions. Lives as a Basic Memory note at the project root or as a global file at `~/.basic-memory/basic-memory.md`. Reserved sections: `## Projects`, `## Placements`, `## Formats`, `## Schemas`. H3 sub-sections provide project-specific overrides; bare H2 content is the default. Section-level fallback between project, global, and built-in defaults.
- **Bootstrap pattern** — documented conversational flow for generating a starter `basic-memory.md` from an existing project's structure. No new command; just ask Claude.

### Removed

- **`validate-memo` skill** and the entire integration with `basic-memory-hooks` (the external validation server). The model-driven approach replaces external validation.
- **`edit-note-local` skill** — its core dependency (`basic-memory sync --watch` running as a separate process) no longer exists; sync now runs automatically inside the MCP server. `edit-note` covers the remaining use cases.
- All references to `localhost:8000` / `localhost:4665`, `basic-memory-hooks`, and `.basic-memory/format.md` across docs and skills.

### Changed

- **`hooks/hooks.json`** — `PreToolUse` on `write_note` now invokes the `placement` skill instead of validate-memo.
- **`commands/remember.md`** — placement is automatic; `[folder]` argument is now an explicit override.
- **`PLUGIN.md`** — new "Configuration" section documenting the unified config schema, precedence, and bootstrap.
- **`README.md`** — removed hooks server quick-start; added pointer to the new configuration model.

### Migration

If you were using `basic-memory-hooks` for memo validation:
- Format rules previously kept in `.basic-memory/format.md` move to a `## Formats` section in `basic-memory.md`. They are now guidance for the model rather than externally enforced.
- The `basic-memory-hooks` server is no longer needed; you can remove it.

If you were using `edit-note-local`:
- Use `edit-note` instead. MCP `edit_note` operations (`append`, `prepend`, `find_replace`, `replace_section`) cover the same workflows.
