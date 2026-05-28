# Basic Memory for Claude Code — Design

**Status:** Draft (v0.4 modernization)
**Supersedes:** the current `PLUGIN.md` feature-list framing
**Related:**
- [SPEC-58: Consolidate Agent Integrations](../../../basic-memory-llc/specs/active/spec-58-consolidate-agent-integrations-into-basic-memory.md) (parent)
- [Plan — Basic Memory on Rails](../../../basic-memory-llc/planning/plan-basic-memory-on-rails.md) (parent)
- `integrations/hermes/` and `integrations/openclaw/` (sibling integrations — same pattern, different agent host)

---

## 1. Positioning — what this plugin actually is

Claude Code now has a working-memory layer of its own: auto-memory at `~/.claude/projects/<path>/memory/MEMORY.md`, plus per-subagent memory at `.claude/agent-memory/`. Claude writes to it itself, loads the first 200 lines / 25 KB at session start, and splits topic files off when MEMORY.md grows too long.

**This plugin is not a memory layer. It is the bridge between Claude's working memory and Basic Memory's durable, semantic, portable graph.**

This is the product's documented stance, not our invention. The official [Basic Memory vs built-in memory](https://docs.basicmemory.com/raw/concepts/vs-built-in-memory.md) page is explicit:

> "Basic Memory doesn't replace them — it works alongside them. **The best setup uses both.**"

Their stated division of labor:
- **Built-in memory** (Claude auto-memory, CLAUDE.md): coding standards, preferences, working instructions — "the immediate operational layer."
- **Basic Memory**: architecture decisions, research, meeting summaries, project context — "persistent, interconnected organizational infrastructure" that's searchable, linkable, and portable across every AI tool.

The plugin is the *mechanism* that makes that documented "use both" setup actually happen automatically, instead of requiring the user to manually shuttle context between the two.

Two memories, two jobs:

|                | Claude auto-memory                      | Basic Memory                                  |
| -------------- | --------------------------------------- | --------------------------------------------- |
| **Where**      | `~/.claude/projects/<path>/memory/`     | Your filesystem + BM graph                    |
| **Who writes** | Claude, automatically                   | Both human and Claude, deliberately           |
| **Scope**      | Per-project, local-only, hidden         | Cross-project, syncable, portable, sharable  |
| **Captures**   | Build commands, debugging tips, patterns | Decisions, research, observations + relations |
| **Loaded**     | Every session, automatically            | Queried on demand                             |
| **Format**     | Whatever Claude finds useful            | Markdown + frontmatter + observations + relations |

1 + 1 = 3 is the thesis: auto-memory tells the plugin "we were here, on this topic, recently"; BM brings the graph neighborhood, open decisions, and active tasks. Neither alone is targeted. Together, the brief is.

## 2. Personas

The plugin serves three personas with the same spine and divergent add-ons.

### The thinker — non-developer power user
Writer, researcher, consultant, planner. Lives in Claude Code or Claude Desktop. No git, no PRs. Keeps a body of work that compounds over months. **Cares about:** picking up where they left off, not losing texture of long conversations, recalling what they actually decided.

### The builder — developer
Uses git, GitHub, runs Claude Code as their primary IDE companion. Same needs as the thinker, plus a code dimension. **Cares about:** tying decisions to commits, surviving multi-hour debugging without amnesia, code archaeology weeks later.

### The operator — PM, team lead, ops
Runs multiple concurrent projects through Claude. Maybe occasional git, mostly meetings, decisions, status. **Cares about:** not bleeding context between projects, recalling cross-project patterns, weekly digests delivered without their attention.

The thinker's needs are the foundation. The builder adds git hooks. The operator adds routines.

## 3. Architecture — the four core surfaces

We keep the plugin to a small number of well-chosen artifacts. Everything else (workflow skills, agents, deep references) loads from the top-level `skills/` package on demand.

```
plugins/claude-code/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── hooks/
│   ├── session-start.sh        # ambient: brief Claude on what's relevant
│   └── pre-compact.sh          # ambient: checkpoint before amnesia
├── output-styles/
│   └── basic-memory.md         # reflexes: search first, capture decisions
│                               # NOTE: rules/ deferred — path-scoped rules don't load yet (Q5)
├── skills/
│   ├── setup/SKILL.md          # /basic-memory:setup — bootstrap interview (first-run)
│   ├── remember/SKILL.md       # /basic-memory:remember <text> — quick deliberate capture
│   └── status/SKILL.md         # /basic-memory:status — show plugin state
├── schemas/                    # picoschema seeds, copied into the user's BM project at bootstrap
│   ├── session.md              # type: session — resume checkpoints
│   ├── decision.md             # type: decision — durable choices + rationale
│   └── task.md                 # type: task — active work tracking
├── settings.example.json       # opinionated defaults, easy to copy
├── DESIGN.md                   # this file
└── README.md                   # rewritten around the bridge story
```

Three layers, matching what Hermes and OpenClaw converged on:

| Layer       | Surface                            | When it fires                              | What it does                          |
| ----------- | ---------------------------------- | ------------------------------------------ | ------------------------------------- |
| Ambient     | `hooks/`, `rules/`                 | Lifecycle events, file context             | Brief Claude, checkpoint, guide placement |
| Background  | `output-styles/basic-memory.md`    | System prompt, every turn                  | Reflexes — search first, capture inline |
| Deliberate  | `skills/{setup,remember,status}/`  | User invokes (`/basic-memory:setup`, `/basic-memory:remember`) | One-shot user gestures                 |

## 4. The core flows

### 4.1 Cold-start resume — SessionStart hook

Fires once per session before the LLM sees anything. Assembles a **targeted briefing** by querying multiple sources in parallel and emitting a structured context block.

**Inputs:** `cwd`, configured BM project(s), and (optionally) auto-memory's `MEMORY.md` read **from disk** — see the timing caveat below.

> **Verified (Q1):** the firing order of `SessionStart` relative to auto-memory load is *not documented*, so we don't assume auto-memory is already in context when the hook runs. If the brief wants to factor in auto-memory's last-topic, the hook reads `~/.claude/projects/<path>/memory/MEMORY.md` directly from disk (always available) rather than relying on it being in context. The 1+1=3 trick still works; it's just a file read, not a context read.

**Parallel queries — structured metadata search, not fuzzy full-text.** This is the multiplier: because the plugin ships schemas (§4.5) that stamp `type` and `status` onto every note it writes, recall is *deterministic*. We ask for exactly the notes that matter, not "things that look textually similar to the cwd."

1. Active tasks: `search_notes("", metadata_filters={"type": "task", "status": {"$in": ["active", "in-progress"]}}, project=<primary>)`
2. Open decisions: `search_notes("", metadata_filters={"type": "decision", "status": "open"}, project=<primary>)`
3. Recent sessions: `search_notes("", metadata_filters={"type": "session"}, after_date=<recallTimeframe>, project=<primary>)` — the last checkpoint carries the resume cursor
4. Recent activity (catch-all, anything untyped): `recent_activity(timeframe=<recallTimeframe>, project=<primary>)`
5. (If team workspaces enabled) Queries 1–3 against team project(s) in parallel

All five run concurrently. Structured filters return precise sets; the catch-all `recent_activity` sweeps up anything not yet schema-typed. The brief leads with the precise sets and falls back to the sweep.

**Output format** (cribbed from OpenClaw, refined):

```markdown
## Basic Memory — context for this session

**Project:** my-app (personal) · 7 days since last activity

### Active tasks
- **Wire SessionStart hook** — outline drafted, hook script next [permalink]
- **Decide on routines for v0.4** — blocked on platform docs [permalink]

### Recent activity (last 3 days)
- DESIGN.md draft for Claude Code plugin (decisions/)
- Notes on Hermes recall pattern (research/)

### Possibly relevant to your current work
- BM project mapping decision (decisions/) — referenced 2x last week

---

You have Basic Memory available. Before answering recall questions ("what did we
decide", "where did we leave off"), search BM first. When the user makes a
material decision, capture it as a DecisionNote inline. Cite permalinks when
referencing prior work.
```

The trailing instruction block is **the recall prompt** — without it, agents ignore injected context. Customizable via `settings.json` (`recallPrompt` key).

**Output mechanism (verified, Q4):** SessionStart injects context two ways — plain stdout (anything the hook prints is added to context, no JSON needed) or JSON `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`. We use **plain stdout** — simpler, fewer parse failures. Two hard constraints:
- **10,000-character cap per output string.** Overflow is spilled to a file and replaced with a preview + path. The brief must stay well under 10k — so recent-activity inclusion is bounded (cap each section at N items; prefer permalinks over previews).
- **Shell-profile interference.** If the user's shell profile echoes anything on startup, it corrupts hook output. The hook must run with a clean environment / guard against profile noise.

**Performance budget:** SessionStart should complete in under 1 second. Parallelize all queries. If BM is unreachable, emit a minimal "BM unavailable; auto-memory only this session" stub and continue — never block the session.

### 4.2 Compaction without amnesia — PreCompact hook

Fires immediately before context compaction. Writes a **SessionNote** (checkpoint) to BM containing what Claude is about to forget.

**Content of the SessionNote:**
- Frontmatter: `type: session`, `started`, `ended_or_compacted`, `project`, `cwd`, `claude_session_id`
- Observations:
  - `[decision]` items surfaced this session
  - `[problem]` / `[attempted]` / `[rejected]` — what we tried that didn't work (critical for resume)
  - `[next-step]` — explicit cursor for the next session
- Relations: `produced [[<note>]]` for each BM write this session
- Open threads: free-form list of unresolved questions

**Where it lands:** `<primary-bm-project>/sessions/<YYYY-MM-DD-HHMM>-<slug>.md`.

**Latency budget (verified, Q2) — much more generous than we assumed.** PreCompact hooks block compaction *synchronously* with a **600-second (10-minute) default timeout** (configurable). A multi-second MCP `write_note` call — or even a full LLM summarization pass — fits comfortably inside that window. This flips our earlier "extractive-only" assumption:

- **Default is now a real summary**, not extractive heuristics. The hook can call out to summarize the transcript into a proper SessionNote. `preCompactCapture: "summarized"` becomes the sensible default; `"extractive"` stays as a fast fallback option.
- The hook does *not* need `decision: block` (we're not preventing compaction, just recording before it). It writes the SessionNote and exits 0, letting compaction proceed.
- One nuance from verification: the timeout cancels the hook if exceeded, and compaction proceeds regardless — so the write should be resilient to being killed mid-flight (write the note early, enrich after, rather than building everything then writing once).

**Optional conformance check (verified, Q8):** the hook may validate the note it just wrote via `schema_validate(identifier=<new-note>)` — the **single-note** path is cheap and bounded (one entity load + one schema-file read). Do **not** call batch `schema_validate(note_type="session")` here — that scans every session note with per-note file I/O (O(N)); leave batch validation and `schema_diff` to the nightly hygiene routine.

### 4.3 The reflex layer — output-style (rules deferred)

**`output-styles/basic-memory.md`** appends to the system prompt:
- Before answering questions that look like recall, call `search_notes` (prefer `metadata_filters` for typed notes) or `build_context`. Don't answer from training.
- When the user makes a material decision, write a `DecisionNote` (`type: decision`, `status: open`) inline as part of your response — conforming to the `decision` schema so it's queryable later.
- When referencing prior work, cite the permalink.
- When auto-memory and BM disagree, flag the conflict explicitly.
- Stay within the active BM project unless the user explicitly asks for another.

The reflexes deliberately target *typed, schema-conforming* writes. A decision captured without `type: decision` is invisible to the next session's structured recall; a decision captured *with* it shows up in the SessionStart brief automatically. The output-style is what makes capture land in the queryable shape.

User opts in via `outputStyle: basic-memory` in their settings. Not auto-applied — this is the user's choice of how Claude behaves.

**Path-scoped rules are deferred (verified, Q5 — refuted).** The original plan was a `rules/basic-memory.md` with `paths:` frontmatter that loads placement/format conventions only when a BM note is in context. Verification found that path-scoped rules **do not load automatically at all** in the current Claude Code (open bug [anthropics/claude-code#16853](https://github.com/anthropics/claude-code/issues/16853), "never worked"), and even when fixed they're repo-relative — they would *not* match BM notes living outside the git tree under `~/basic-memory/` (issue #25562). So we don't ship a path-scoped rule.

**Where placement/format conventions live instead:** the bootstrap-inferred conventions (folder naming, favored observation categories, frontmatter shape) are stored in the `basicMemory` block of `.claude/settings.json` and surfaced two ways:
1. The **SessionStart hook** folds a one-line conventions summary into the brief (within the 10k budget).
2. The **output-style** references "follow the project's stored placement conventions when writing notes."

If/when path-scoped rules start working *and* support out-of-tree globs, we can add a `rules/basic-memory.md` as a third surface. Until then it's dead weight that wouldn't load.

### 4.4 Deliberate gestures — skills

Three skills only, each Claude-Code-specific (everything else lives in top-level `skills/`).

> **Verified (Q3) — slash commands are always plugin-namespaced.** A skill folder `skills/remember/` in a plugin whose `plugin.json` name is `basic-memory` is invoked as **`/basic-memory:remember`**, not `/remember` — namespacing is mandatory and can't be shortened. Consequence: we drop the redundant `bm-` prefix from skill folder names (the namespace already says `basic-memory`). So the folders are `setup/`, `remember/`, `status/`, surfacing as `/basic-memory:setup`, `/basic-memory:remember`, `/basic-memory:status`. Skills are auto-discovered on install — no extra registration.

**`/basic-memory:setup`** — bootstrap interview (§7). Run after install and any time the user wants to reconfigure.

**`/basic-memory:remember <text>`** — quick capture. Writes to a `bm-remember/` folder, separated from auto-captures. First line becomes title (truncated to 80 chars), tagged `manual-capture`. Optional `--project` flag for cross-project.

**`/basic-memory:status`** — show plugin state: active BM project, capture folders, recent SessionNotes, sync status, last successful BM call. Trust-building UI.

### 4.5 The schema layer — why our note types are contracts, not conventions

Basic Memory ships a [schema system](https://docs.basicmemory.com/raw/concepts/schema-system.md) (Picoschema) and [structured metadata search](https://docs.basicmemory.com/raw/concepts/metadata-search.md). The plugin leans on both. This is the single biggest "leverage what BM already does" decision in the design.

**The plugin ships schemas for the note types it creates.** Three to start:

| Note type | `type:` | Written by | Purpose |
| --------- | ------- | ---------- | ------- |
| Session   | `session`  | PreCompact hook, `/basic-memory:handoff` (future) | Resume cursor — what we were doing, what's next |
| Decision  | `decision` | output-style reflex, `/basic-memory:decide` (future) | Durable record of choices + rationale |
| Task      | `task`     | user + Claude | Active work tracking (aligns with `skills/memory-tasks`) |

These conform to the SessionNote / DecisionNote picoschema shapes defined in SPEC-55, so when the SPEC-55 Writer SDK and async pipeline land, validation Just Works and nothing has to change in user-facing behavior.

**Why schemas, not just "write notes with a convention":**

1. **Deterministic recall.** A schema stamps `type` and `status` into frontmatter. SessionStart (§4.1) then queries `metadata_filters={"type":"session"}` and gets *exactly* the sessions — no fuzzy matching, no false positives, no missed notes. Structured search is AND-composable: `{"type":"decision","status":"open","tags":["auth"]}` returns open auth decisions, full stop. This is the difference between a recall brief that's *precise* and one that's *vibes*.

2. **The schema teaches the structure.** Picoschema fields map to observations *and* relations *and* frontmatter:

   ```yaml
   # schemas/session.md
   ---
   title: Session
   type: schema
   entity: session
   version: 1
   schema:
     summary: string, one-paragraph what-happened
     next_step?(array): string, cursor for resuming
     decision?(array): string, decisions made this session
     problem?(array): string, problems hit (incl. attempted-and-rejected)
     produced?(array): Entity, notes created this session   # → `produced [[note]]` relations
   settings:
     validation: warn
     frontmatter:
       project: string
       cwd?: string
       started: string
       status?(enum): [open, resumed, closed]
       claude_session_id?: string
   ---
   ```

   The PreCompact hook and the output-style don't need to memorize the SessionNote shape — they read it from the schema. The schema is the single source of truth for "what a good checkpoint looks like."

3. **Drift detection becomes a hygiene routine.** `schema_diff` flags when session/decision notes start diverging from their schema over time. A future nightly routine can run it and surface drift. `schema_validate` can run in the PreCompact hook to confirm the checkpoint we just wrote actually conforms before we rely on it next session.

4. **Bootstrap can infer, not dictate.** `schema_infer` analyzes the user's *existing* notes and suggests schemas/conventions. The bootstrap interview (§7) runs it to seed the stored placement/format conventions (in the `basicMemory` settings block — see §4.3, since path-scoped rules don't load yet) with the user's real patterns instead of imposing ours. Opinionated defaults, flexible to what's already there.

**Validation mode: `warn`, never `strict`.** We stamp structure to make recall work, but we never block a write because a field is missing. The user's flow is sacred; the schema is a helper, not a gate. Users who want strict validation flip it themselves.

**Where schemas live:** the plugin's bootstrap writes them into the primary BM project's `schemas/` folder (`schemas/session.md`, `schemas/decision.md`, `schemas/task.md`) — they become normal, editable BM notes the user owns, not hidden plugin internals. If the user already has schemas for these types, bootstrap leaves them alone.

## 5. Claude Code projects ↔ Basic Memory projects

These are orthogonal concepts that the plugin must explicitly map.

**Claude Code project** = a cwd-keyed working directory (typically a repo). Auto-memory uses this as its key.

**Basic Memory project** = a logical knowledge base. Could be 1:1 with a repo, could be cross-cutting, could be unrelated.

### 5.1 Mapping model

Each Claude Code project has:
- **One primary BM project** — destination for SessionStart context + PreCompact checkpoints + `/basic-memory:remember`. Required.
- **Zero or more secondary BM projects** — read-only by default for recall (SessionStart can query them); writes require explicit user gesture.

Mapping is configured in `.claude/settings.json`:

```json
{
  "basicMemory": {
    "primaryProject": "my-app",
    "secondaryProjects": ["team-engineering", "personal/notes"],
    "captureFolder": "sessions",
    "rememberFolder": "bm-remember",
    "recallPrompt": "...",
    "recallTimeframe": "3d"
  }
}
```

Resolution order for the primary project when the setting is absent:
1. `.claude/settings.local.json` (per-user override)
2. `.claude/settings.json` (team-committed default)
3. `basic-memory` note at the repo root (legacy, kept for back-compat)
4. User's global default project (from BM CLI: `bm project list --default`)
5. Trigger bootstrap interview

### 5.2 The 1:1 default is fine but not enforced

For most thinker-persona users, one repo ↔ one BM project is natural. For developers and operators, fan-out is common (one BM project spans multiple repos: a "company-knowledge" project that aggregates context from every code repo). The plugin should not force 1:1.

## 6. Team / shared workspaces

Basic Memory Cloud has **workspaces** (each an org or personal space) containing
**projects**. Some users contribute to *shared* projects visible to teammates. This
needs deliberate, safe-by-default design.

**Routing reality (verified against a real two-workspace account):** project names
**collide across workspaces** — e.g. `main` and `getting-started` exist in more than
one. A bare name won't route. Every cross-workspace ref in config must therefore be a
**workspace-qualified name** (`<workspace-slug>/<project>`, e.g. `my-team-2/notes`,
from the `qualified_name` field) or an **`external_id` UUID** (most stable — survives
renames). The CLI accepts these via `--project` / `--project-id`; the hook detects a
UUID and routes accordingly. Cross-workspace reads route over the user's OAuth session
(no API key needed) — confirmed working for both structured search and recent-activity.

### 6.1 Defaults — safe by design

- **Auto-capture defaults to personal.** SessionNotes, PreCompact checkpoints, and
  `/basic-memory:remember` quick captures **only ever** land in `primaryProject`. The
  capture hooks never write to a shared project — full stop, no opt-in flag in v0.4.
- **Recall reads across.** SessionStart queries the shared projects in parallel for
  open decisions and folds them into the brief (read-only — discloses nothing).
- **Sharing is a deliberate gesture.** `/basic-memory:share` copies a personal note
  into a configured team project with attribution, after explicit confirmation. The
  personal→team boundary is always a visible, manual action.

### 6.2 Configuration shape

```json
{
  "basicMemory": {
    "primaryProject": "basic-memory-7020…/main",
    "secondaryProjects": ["my-team-2/main", "my-team-2/notes"],
    "teamProjects": {
      "my-team-2/notes": { "promoteFolder": "shared" }
    }
  }
}
```

- `secondaryProjects` — workspace-qualified refs (or UUIDs) read for recall. Read-only.
- `teamProjects` — share targets for `/basic-memory:share`; each carries a
  `promoteFolder` (default `shared`). Also read for recall (SessionStart reads the
  union of `secondaryProjects` and `teamProjects` keys, capped at 6 per session).

**`autoWrite` is deferred.** An earlier draft proposed `teamProjects.<name>.autoWrite`
to let auto-capture write to a team project. v0.4 does **not** implement it — auto-capture
stays personal and sharing is always manual. Revisit only if a team explicitly wants
shared session memory; until then we don't ship a flag we don't enforce.

### 6.3 What this unlocks

- Team-engineering project becomes a *living memory* shared across the team. Recent decisions, ADRs, debugging notes from any teammate's session show up in everyone's SessionStart brief.
- New team members get instant context — first SessionStart pulls the team graph and they're already oriented.
- Cross-pollination — operator running a strategy session sees recent technical decisions from builders.

## 7. Bootstrap — `/basic-memory:setup` interview

Users get overwhelmed starting from zero. The plugin opens with a guided interview that establishes opinionated defaults from a short conversation.

> **Verified (Q6) — there is no install hook.** Claude Code has no PostInstall/PreInstall lifecycle event (feature request [#11240](https://github.com/anthropics/claude-code/issues/11240) was closed as duplicate). We can't auto-run setup the moment the plugin installs. The workaround is the **SessionStart hook detecting first-run**: it checks for a sentinel (e.g. `${CLAUDE_PLUGIN_DATA}/.bootstrapped` or the absence of a `basicMemory` config) and, if missing, injects a one-line nudge — *"Basic Memory isn't configured yet. Run `/basic-memory:setup` (≈3 min) to wire it up."* A bonus verified capability: SessionStart can return `{"reloadSkills": true}` to re-scan skills mid-session, useful if setup writes new skills/config that should activate without a restart.

**Trigger paths:**
1. SessionStart detects no `basicMemory` config and no `basic-memory` note → inject the one-line nudge suggesting `/basic-memory:setup` (we cannot run it automatically)
2. User runs `/basic-memory:setup` explicitly (anytime, including for reconfiguration)

**Interview script** (Claude executes it; SKILL.md provides the structure):

1. *"Quick setup — about 3 minutes. What's this Claude Code project mostly about? (code / writing / research / planning / mixed)"*
2. *"Do you have a Basic Memory project for this already, or should I create one?"*
   - If existing: offer `list_memory_projects()` output to pick from
   - If new: ask name and folder (default: `~/basic-memory/<repo-name>/`)
3. *"Are you using Basic Memory Cloud or local-only?"*
   - If cloud + team workspace exists: *"You're on the `<team>` workspace. Want me to also read from team projects for recall? (read-only by default; we can opt-in to writes later)"*
4. *"How chatty should I be?"*
   - **Light** (default): SessionStart brief on each session, PreCompact checkpoint, `/basic-memory:remember` on demand
   - **Standard**: above + capture decisions inline via output-style
   - **Heavy**: above + every-session SessionNote even without compaction
5. *"Should I look at your existing notes and suggest some placement conventions?"* (yes → runs `schema_infer` on the existing notes, summarizes the patterns it found, and stores them in the `basicMemory` settings block — see §4.3, since path-scoped rules don't load yet — from the user's *real* conventions rather than imposed ones)
6. *"I'll set up schemas for session checkpoints and decisions so I can find them precisely later — okay?"* (yes → writes `schemas/session.md`, `schemas/decision.md`, `schemas/task.md` into the primary project, skipping any that already exist; validation mode `warn`)
7. *"Want me to enable the `basic-memory` output style now?"* (yes → adds `outputStyle: basic-memory` to settings)

**Output:** writes `.claude/settings.json` (with prompt to commit or keep local) including any inferred conventions, writes the three schema notes, optionally creates the BM project, and drops the bootstrap sentinel so SessionStart stops nudging. Closes with: *"Done. I'll start using this on the next message. Try `/basic-memory:status` anytime to see what I'm tracking."*

**Why an interview, not a config form:** the interview is *adaptive* — it skips questions when context is obvious (e.g., it sees you're on cloud and on a team; doesn't ask about local), suggests reasonable defaults the user can accept with a single word, and produces a meaningful starting point in under 3 minutes. A config form makes the user own every decision.

## 8. Configuration — opinionated defaults

`settings.example.json` ships the defaults; user copies to `.claude/settings.json` and tweaks. Bootstrap writes this automatically.

```json
{
  "basicMemory": {
    "primaryProject": null,
    "secondaryProjects": [],
    "captureFolder": "sessions",
    "rememberFolder": "bm-remember",
    "recallTimeframe": "3d",
    "recallPrompt": "You have Basic Memory available. Search before answering recall questions. Capture material decisions inline. Cite permalinks when referencing prior work.",
    "captureChattyness": "light",
    "preCompactCapture": "summarized",
    "placementConventions": null,
    "teamProjects": {}
  },
  "outputStyle": "basic-memory"
}
```

`preCompactCapture` defaults to `"summarized"` — verified (Q2) that PreCompact's 600s budget is ample for an LLM summarization pass; `"extractive"` remains as a fast fallback. `placementConventions` holds the bootstrap-inferred placement/format guidance (§4.3) since path-scoped rules don't load yet. Everything is overridable. Nothing is mandatory.

## 9. What's out of scope (for v0.4)

These come later or never:

- **Per-turn capture into BM.** Auto-memory already does the per-turn working summary. Doubling it into BM creates noise without value.
- **Commit-hook integration (entire.io-style).** Worthy idea, but defer to a builder-add-on after the spine is proven.
- **Routines.** Defer to a separate doc + phase. Once the spine works, routines become "weekly digests, nightly hygiene, GitHub webhooks."
- **Statusline integration.** Polish; deferred.
- **Subagent memory bundling.** The platform now offers `memory: project|user|local` on subagents. Worth exploring but doesn't fit the spine.
- **Replacement of `basic-memory-manager` agent.** Plugin ships no agent in v0.4. Users who want one install from a separate package.

## 10. What this replaces / deletes

From the current plugin (v0.3.x):
- All six plugin-local skills (`continue-conversation`, `edit-note`, `knowledge-capture`, `knowledge-organize`, `placement`, `research`) → migrate workflows to top-level `skills/`, deprecate local copies
- `basic-memory-manager.md` agent → deleted; mention in CHANGELOG that users wanting it should install from `skills/` package
- `PreToolUse: write_note` hook (the "echo additionalContext" hook) → deleted; placement guidance moves to the `basicMemory` settings block + output-style (§4.3 — path-scoped rules don't load yet)
- `PostToolUse: write_note` confirmation echo → deleted (noise)
- `basic-memory` config-note convention → deprecated in favor of `.claude/settings.json` (kept as fallback for one release)
- 314-line `PLUGIN.md` feature list → replaced by a short `README.md` framed around the bridge story

## 11. Verified findings (resolved open questions)

Verified 2026-05-28 against Claude Code v2.1.153 and basic-memory 0.21.5, via a fan-out investigate → adversarial-verify workflow (each finding independently confirmed/refuted from a second angle). Verdicts: **confirmed** = both passes agree; **refuted** = verify pass overturned the first answer; **uncertain** = could not be confirmed, treat with caution.

| # | Question | Verdict | Answer | Design consequence |
|---|----------|---------|--------|--------------------|
| **Q1** | Does SessionStart fire before/after auto-memory loads? Can the hook use auto-memory as input? | **uncertain** | Firing order vs `MEMORY.md` load is **not documented**. The often-cited "SessionStart fires before CLAUDE.md loads" phrasing isn't in current docs. What *is* certain: the hook can read `MEMORY.md` from disk anytime. | Don't assume auto-memory is in context at SessionStart. If the brief wants it, **read the file from disk** (§4.1). Don't build anything that depends on context-load ordering. |
| **Q2** | Does PreCompact block synchronously? Timeout? Can it do multi-second/LLM work? | **confirmed** | Blocks synchronously; **600s default timeout** (configurable). MCP/LLM calls fit easily. On timeout the hook is killed and compaction proceeds. (To *block* compaction you'd return exit 2 / `decision:block` before the timeout — we don't.) | **Upgraded the design.** `preCompactCapture` default is now `"summarized"` (real LLM pass), not extractive (§4.2, §8). Write the note early, enrich after, in case of kill. |
| **Q3** | Do plugin skills/commands appear in the `/` menu, and as what? | **confirmed** | Auto-discovered on install, but **always namespaced** as `/<plugin-name>:<skill>`. Can't be shortened. No sparse/subdir caveats. | Drop the `bm-` prefix; folders become `setup/`, `remember/`, `status/` → `/basic-memory:setup` etc. (§4.4). README must show the namespaced form. |
| **Q4** | How does SessionStart inject context? Size limit? | **confirmed** | Plain stdout (added to context, no JSON needed) **or** JSON `hookSpecificOutput.additionalContext`. **10,000-char cap** per string; overflow spills to a file. Shell-profile echoes corrupt output. | Use plain stdout. Keep the brief well under 10k — cap each section's item count, prefer permalinks over previews. Guard against shell-profile noise (§4.1). |
| **Q5** | Do path-scoped `rules/` with `paths:` load for BM files (incl. outside the git tree)? | **refuted** | Path-scoped rules **don't load automatically at all** — open bug [#16853](https://github.com/anthropics/claude-code/issues/16853) ("never worked"). Even when fixed they're repo-relative (won't match `~/basic-memory/`, [#25562](https://github.com/anthropics/claude-code/issues/25562)). | **Dropped `rules/` from the plugin.** Placement/format conventions move to the `basicMemory` settings block + SessionStart brief + output-style (§4.3). Revisit if the platform bug is fixed *and* out-of-tree globs are supported. |
| **Q6** | Is there a run-on-install hook for bootstrap? | **confirmed** | No PostInstall/PreInstall lifecycle event ([#11240](https://github.com/anthropics/claude-code/issues/11240) closed as dup). Only SessionStart + explicit Setup. Bonus: SessionStart can return `{"reloadSkills": true}`. | Bootstrap can't auto-run. SessionStart detects first-run via a **sentinel file** and nudges the user to run `/basic-memory:setup` (§7). |
| **Q7** | Does `search_notes` support `metadata_filters` + `after_date` in 0.21.5? Min version? | **confirmed** | Both present and working in 0.21.5. Full operator set: `$in`, `$gt/$gte/$lt/$lte`, `$between`, array-contains, equality, dot-notation (`schema.confidence`). On `search_notes` since **v0.18.1** (verify corrected the first pass's "v0.19.0"). | Structured recall is safe as a **baseline** — no fallback path needed for the 0.21.5 target. Pin a minimum `basic-memory >= 0.19.0` in prerequisites for margin. Use `tags`/`status` shorthands for common cases (§4.1). |
| **Q8** | Is `schema_validate` cheap enough for PreCompact? Are the schema tools in 0.21.5? | **confirmed** | All three (`validate`/`infer`/`diff`) present since v0.19.0. `schema_validate(identifier=…)` = **cheap single-note**. `schema_validate(note_type=…)` = **batch, O(N)** with per-note file I/O. | PreCompact validates only the note it just wrote via the **identifier path** (§4.2). Batch validation + `schema_diff` go to the nightly hygiene routine (§13). |

**Net effect on the design:** two findings made it *better* (Q2 → real LLM summaries at compaction; Q7 → structured recall is a safe baseline), two forced corrections (Q5 → no path-scoped rules; Q3 → namespaced commands), and one stays a known unknown to design around (Q1 → read auto-memory from disk, never assume context order).

## 12. Task list — v0.4 milestone

### Phase 0: Verify open questions — ✅ DONE (2026-05-28)
- [x] SessionStart timing vs auto-memory (Q1 — uncertain; read MEMORY.md from disk)
- [x] PreCompact latency budget (Q2 — 600s, LLM summary feasible)
- [x] Slash-command discovery + namespacing (Q3 — `/basic-memory:<skill>`)
- [x] `additionalContext` size limit (Q4 — 10k chars, plain stdout)
- [x] Path-scoped rules behavior (Q5 — refuted; rules don't load, dropped)
- [x] Bootstrap-on-install (Q6 — no install hook; SessionStart sentinel)
- [x] `metadata_filters`/`after_date` in 0.21.5 (Q7 — confirmed, baseline-safe)
- [x] `schema_validate` cost (Q8 — single-note cheap; batch → routine)
- [x] Findings recorded in §11

### Phase 1: The spine — ✅ DONE (2026-05-28)

**Build decisions (user calls):** *minimal-first vertical slice* (one fast query at
SessionStart, extractive PreCompact — prove the loop, enrich later) and *hard-delete*
the old surfaces (no coverage audit, clean break). Hooks implemented in **bash +
python3** (python is a guaranteed BM dependency; the multi-query Python single-session
helper is the enrich step). `preCompactCapture` ships as `"extractive"` even though the
eventual default is `"summarized"` — the LLM pass is the first enrich step.

- [x] Delete deprecated artifacts — six skills, `basic-memory-manager` agent, both
  `write_note` hooks, `PLUGIN.md`, config-note convention
- [x] Write `schemas/{session,decision,task}.md` (picoschema, `validation: warn`;
  `task` mirrors `memory-tasks`)
- [x] Validate the three schemas — confirmed they parse through BM's exact path
  (`frontmatter.loads` → `parse_schema_note` → `parse_picoschema`). **Found + fixed an
  enum-syntax bug**: enum descriptions must go *inside* the parens
  (`status?(enum, desc): [a,b]`), not after the `[...]` value — the latter is invalid
  YAML. (Same latent bug exists in the canonical `memory-tasks` schema → flagged as a
  separate task.)
- [x] Write `hooks/session-start.sh` — plain-stdout brief, single `type: task` /
  `status: active` query, recall prompt; works against the default project (pin via
  `primaryProject`); silent if BM absent. Tested end-to-end.
- [x] Write `hooks/pre-compact.sh` — **extractive** checkpoint conforming to the
  `session` schema; only writes when `primaryProject` is set; silent/no-op otherwise.
  Tested end-to-end (note written + queryable by `--type session`).
- [x] Write `output-styles/basic-memory.md` — reflexes; `keep-coding-instructions: true`
  so it composes with dev work.
- [x] ~~Write `rules/basic-memory.md`~~ — **cut** (Q5). Conventions go in `basicMemory`
  settings + the brief.
- [x] Write `settings.example.json` (ships `preCompactCapture: "extractive"` for parity
  with the implemented hook; `placementConventions` reserved)
- [x] Update `.claude-plugin/plugin.json` + both `marketplace.json` descriptions (bridge
  framing; `name: basic-memory` confirmed for namespacing). Version left to release tooling.
- [x] Rewrite `scripts/validate_claude_plugin.py` for the new layout (hooks +
  output-style + schemas; agent dropped; skills optional). Passes `ci-check` and
  `claude plugin validate . --strict`.
- [x] Update plugin `CHANGELOG.md` + the `AGENTS.md` package-check description
- [x] Add `basic-memory >= 0.19.0` to prerequisites (Q7 margin)
- [ ] `update_versions.py` — no change needed (no new *versioned* manifests; hooks /
  schemas / output-style / settings carry no version)

**Carried into later phases (not part of the minimal cut):** the first-run *sentinel
nudge* moves to Phase 3 (it should point at `/basic-memory:setup`, which doesn't exist
yet); the multi-query parallel brief and the LLM-summarized PreCompact are the enrich
steps.

**Implementation finding for Phase 3 bootstrap (corrected in Phase 3):** an earlier
Phase 1 note claimed the CLI `write-note` couldn't seed a resolvable schema and that
bootstrap must use the MCP `write_note(note_type="schema", metadata=…)` path. That was
**confounded by the enum-syntax YAML bug** — at the time of that test the schema
frontmatter didn't parse, so it couldn't index. Re-verified in Phase 3 with the fixed
schemas: writing a schema file's content via `write_note` (CLI or MCP, embedded
frontmatter) indexes it as `type: schema` **and** `schema_validate` resolves it
(`schema_entity: "Session"`, `valid_count: 1`). So **schema seeding is just a content
copy** — bootstrap reads each `schemas/*.md` and writes it via `write_note`; no
`note_type`/`metadata` gymnastics needed.

### Phase 2: Deliberate gestures — ✅ DONE (2026-05-28)

Both skills implemented as **prose skills** (skills are prompts) rather than
bash-injection scripts — avoids shell-quoting fragility on arbitrary user text and
the `${CLAUDE_SKILL_DIR}` path uncertainty, and works regardless of the MCP server's
tool-name prefix.

- [x] Write `skills/remember/SKILL.md` → `/basic-memory:remember` — model-invocable
  ("remember that…"); writes verbatim to `rememberFolder` with a first-line title and
  `manual-capture` tag via `write_note`.
- [x] Write `skills/status/SKILL.md` → `/basic-memory:status` — `disable-model-invocation`
  (user-only diagnostic); reports project, folders, output-style, recent checkpoints,
  active-task count.
- [x] Test slash-command discovery end-to-end — installed from a local marketplace and
  confirmed via `claude plugin details`: **Skills (2): remember, status**, namespaced
  as `/basic-memory:<name>` (validates Q3 live). Cleaned up afterward.
- [x] Validator now requires the shipped skill set (`REQUIRED_SKILLS`); passes
  `claude plugin validate . --strict`.

### Phase 3: Bootstrap interview — ✅ DONE (2026-05-28)

Implemented as a **prose skill** (the interview is conversational; Claude runs it
using its MCP tools). Verified the whole loop end-to-end against throwaway projects.

- [x] Write `skills/setup/SKILL.md` → `/basic-memory:setup` — adaptive interview that
  maps the project, seeds schemas, optionally learns conventions, writes settings, and
  enables the output style. Model-invocable ("set up basic memory") + user-invocable.
- [x] Wire `schema_infer`/`list_directory` into the bootstrap — the skill inspects the
  folder layout + samples notes, summarizes the user's real conventions, and stores the
  summary string in `basicMemory.placementConventions` (infer, don't dictate).
- [x] Wire schema-seeding — the skill reads `<plugin>/schemas/*.md` (two dirs up from
  the skill) and writes each via `write_note` (content copy, skip-if-exists). **Verified
  end-to-end:** all three seed, index as `type: schema`, and resolve via
  `schema_validate` (`entity=Session/Decision/Task`). This corrects the earlier Phase 1
  finding — schema seeding is a plain content copy; the previous "must use
  `note_type`/`metadata`" conclusion was confounded by the enum YAML bug.
- [x] First-run detection in SessionStart — nudges toward `/basic-memory:setup` when no
  `basicMemory` config block exists (config presence is the sentinel; no separate file).
  The nudge survives a failed/empty task query. Verified across all three config states
  (no config → nudge; block without project → pin tip; project pinned → silent).
- [x] `{"reloadSkills": true}` — **not needed.** Setup adds no new skill files mid-session
  (the skills already ship with the plugin); config changes take effect when the hooks
  next run. Documented as N/A rather than implemented.
- [x] Tests for inferred-conventions — the inference is **model-driven** (prose skill),
  so there's no deterministic code unit to unit-test. The testable contract is the
  skill's presence + frontmatter, enforced by `validate_claude_plugin.py`'s
  `REQUIRED_SKILLS` (now `setup`, `remember`, `status`). Real validation is dogfooding.

### Phase 4: Team workspace support — ✅ DONE (2026-05-28)

Grounded in a real two-workspace BM Cloud account (verified name-collision routing,
OAuth cross-workspace reads). Pulled `/basic-memory:share` forward from future-work
since team usage needs a safe write path.

- [x] Extend SessionStart to read primary + shared projects **in parallel** —
  `ThreadPoolExecutor` over structured queries (primary: active tasks + open
  decisions; each shared project: open decisions). Routes by qualified name or UUID,
  per-call timeout, capped at 6 shared projects, graceful on any failure. Verified
  against the real `my-team-2` workspace and with local fixtures.
- [x] Add `/basic-memory:share` (`skills/share/`) — the deliberate personal→team
  write: reads a note from `primaryProject`, confirms, and copies it to a configured
  `teamProjects` target's `promoteFolder` with `shared_from` attribution. Preserves
  the note's type so shared decisions stay findable in the team's structured recall.
- [x] Config: `secondaryProjects` (read sources) + `teamProjects` (share targets with
  `promoteFolder`). Documented in `settings.example.json` with the qualified-name
  requirement. `autoWrite` deliberately **not** shipped (see §6.2). `REQUIRED_SKILLS`
  now includes `share`.
- [x] `status` reports team read-sources + share targets; `setup` interview step 3
  now configures them via `list_workspaces` + qualified names.
- [x] Document the share-vs-capture distinction (README "Teams" section + the
  read-only note in the SessionStart brief itself).

### Phase 5: Docs + dogfood

Docs done 2026-05-28; dogfood is the remaining (human) step.

- [x] Rewrite `README.md` around the bridge story (done in Phase 1; expanded with
  Commands + Teams sections in Phases 2-4).
- [x] `docs/why-combine-memory.md` — user-facing value prop, the three personas, use
  cases, and the "use both" framing.
- [x] `docs/getting-started.md` — guided install → setup → see-it-work → team walkthrough.
- [x] `docs/architecture.md` — flow-by-flow with mermaid diagrams (the bridge,
  SessionStart, PreCompact, capture reflexes, team read/share, component map).
- [x] Linked all three from the README's Documentation section.
- [x] ~~Migration guide~~ — **not needed.** v0.4 is a clean break; upgrade = uninstall
  the old plugin, install the new one. Noted in the CHANGELOG's "Removed" section.
- [ ] Internal dogfood — full team uses v0.4 for a week of normal work (incl. the team
  workspace path); file bugs against rough edges.
- [ ] Refine the recall prompt and SessionStart brief format based on real sessions.
- [ ] (Likely follow-ups surfaced by dogfood) the multi-query brief enrichment and the
  LLM-summarized PreCompact checkpoint.

### Phase 6: Release
- [ ] Bump version in lockstep with basic-memory release
- [ ] Update CHANGELOG
- [ ] `just package-check-claude-code` passes
- [ ] Tag and ship

## 13. Future work (post-v0.4)

- **Routines integration** — three routine templates (nightly hygiene, weekly digest, daily reflection). Separate design doc. The nightly hygiene routine is the natural home for `schema_diff` drift detection and the deferred LLM-summary pass over the day's extractive SessionNotes.
- ~~**`/basic-memory:share`** — promote personal note → team project~~ — shipped in Phase 4.
- **Team `autoWrite`** — opt-in for auto-capture (PreCompact/remember) to write to a
  team project, for teams that want shared session memory. Deferred from Phase 4 (§6.2).
- **`/basic-memory:blame <sha>`** — code archaeology, builder add-on.
- **Commit-hook integration** — PostToolUse on `Bash(git commit *)` writes CommitNote linking SHA to session's BM writes.
- **Subagent memory bundling** — explore `memory: project|user` on dedicated BM subagents.
- **Statusline** — small visible presence (active project, last write).
- **`/basic-memory:promote`** — review auto-memory MEMORY.md, graduate observations into BM with proper schema.
