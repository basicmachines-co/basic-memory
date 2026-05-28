---
name: basic-memory
description: Use the Basic Memory knowledge graph for persistent memory across sessions. Search before answering; capture decisions, meetings, and insights as notes.
category: memory
---

# Basic Memory Knowledge Graph

You have access to a persistent knowledge graph backed by Basic Memory. The graph survives across sessions and is shared with other tools (Claude Desktop, Obsidian, the `bm` CLI). Use the `bm_*` tools below to recall and capture information.

## Use `bm_*`, not the `bm` CLI

**Always invoke the `bm_*` tools directly. Do not shell out to the `bm` CLI for note operations.**

The `bm_*` tools route through a persistent MCP connection — roughly 0.1 seconds per call. Running `bm` from the shell spawns a fresh Python process per call (1-2 seconds of cold-start every time) and bypasses Hermes's automatic per-turn capture, so the session-transcript and summary notes won't reflect what you did.

The CLI is fine when you genuinely need a feature these wrappers don't expose (rare). Otherwise, prefer:

| Use case | Tool (not CLI) |
|---|---|
| Search the graph | `bm_search` |
| Read a note | `bm_read` |
| Create / update a note | `bm_write` / `bm_edit` |
| Navigate relations | `bm_context` |
| Move / delete | `bm_move` / `bm_delete` |
| What's been touched lately | `bm_recent` |
| List available projects | `bm_projects` |
| List cloud workspaces | `bm_workspaces` |

## Tool reference

### `bm_search` — search the graph
Use **before** answering questions about prior decisions, projects, meetings, or anything that might already be documented.

```
bm_search({ query: "auth strategy decision", limit: 5 })
```

### `bm_read` — fetch a note's full content
After search shows a relevant note, read it for context.

```
bm_read({ identifier: "decisions/auth-strategy" })
bm_read({ identifier: "memory://projects/api-redesign" })
```

### `bm_context` — navigate via memory:// URLs
Returns the target note plus related notes via traversed relations.

```
bm_context({ url: "memory://projects/api-redesign", depth: 1 })
```

### `bm_write` — capture new knowledge
When the user shares a decision, meeting outcome, or insight worth keeping, capture it. Use clear titles and a folder.

```
bm_write({
  title: "API Authentication Decision",
  folder: "decisions",
  content: "# API Authentication\n\n## Context\n...\n\n## Decision\n..."
})
```

Recommended folders: `projects/`, `decisions/`, `meetings/`, `concepts/`, `weekly/`.

### `bm_edit` — incremental updates
Operations: `append`, `prepend`, `find_replace` (requires `find_text`), `replace_section` (requires `section`).

```
bm_edit({
  identifier: "projects/api-redesign",
  operation: "append",
  content: "\n## Update 2026-05-09\nDeployed to staging."
})
```

### `bm_delete` / `bm_move` — maintenance
Use sparingly. `bm_move` takes `new_folder`.

### `bm_recent` — what's been touched lately
Returns notes updated within a window. Use when there's no specific query yet — e.g. "what was I working on yesterday?"

```
bm_recent({ timeframe: "7d" })
bm_recent({ timeframe: "yesterday", limit: 20 })
bm_recent({ timeframe: "2 weeks", type: "entity" })
```

`timeframe` accepts natural language (`"yesterday"`, `"2 weeks"`, `"last month"`) or compact forms (`"7d"`, `"24h"`). Default is `7d`.

### `bm_projects` — list available projects
Returns name, workspace slug, and `external_id` (UUID) per project across local and cloud. Call this when the user names a project that isn't the active one. Route follow-up tool calls either by workspace-qualified name (`project: "personal/main"`) or by UUID (`project_id: "bf2a4c1e-d77f-..."`) — see Cross-project routing below.

```
bm_projects()
```

### `bm_workspaces` — list BM Cloud workspaces
Workspaces are a BM Cloud concept. Returns name, type, role, and default flag. Pair with `bm_projects` when the same project name might exist in more than one workspace and you need to disambiguate.

```
bm_workspaces()
```

## Permalinks

A permalink is the canonical, URL-friendly identifier for a note. Three shapes exist; the read/write tools accept all of them:

| Shape | Example | When |
|---|---|---|
| **Short** | `decisions/auth-strategy` | Bare `folder/note-slug`. Tools need a `project` (or `project_id`) arg to route — the permalink alone isn't enough. |
| **Project-qualified** | `main/decisions/auth-strategy` | `project-name/folder/note-slug`. Carries enough context to route without a separate `project` arg. |
| **Workspace-qualified** | `personal/main/decisions/auth-strategy` | `workspace-slug/project-name/folder/note-slug`. Fully routes, including across cloud workspaces with same-named projects. |

**Important: the permalink returned by `bm_write` already encodes the routing it needs for follow-up reads.** If you wrote with `project="personal/main"`, you get back `personal/main/folder/note-slug` and can call `bm_read({ identifier: <that permalink> })` with no `project` arg. The permalink self-routes.

`memory://` URLs follow the same shapes: `memory://personal/main/decisions/auth-strategy` is valid. The `memory://` prefix is optional for `bm_read` (any of the three permalink shapes works directly); `bm_context` expects the prefix.

## Cross-project routing

Every read/write tool (`bm_search`, `bm_read`, `bm_write`, `bm_edit`, `bm_context`, `bm_delete`, `bm_move`, `bm_recent`) accepts optional `project` and `project_id`:

- `project` — project name, optionally workspace-qualified. Plain (`"main"`) when the name is globally unique; qualified (`"personal/main"`, `"team-paul/research"`) when you need to pick a specific cloud workspace by slug.
- `project_id` — UUID from `bm_projects` (`external_id` field). The most stable identifier — survives project renames and works across workspaces without qualification. Wins over `project` if both are passed.

Omit both and the call uses the Hermes-configured active project.

```
# Plain project name (unique)
bm_write({ title: "...", folder: "...", content: "...", project: "main" })

# Workspace-qualified name (disambiguates same-named projects across workspaces)
bm_write({ title: "...", folder: "...", content: "...", project: "personal/main" })

# UUID (most stable, survives renames)
bm_write({ title: "...", folder: "...", content: "...", project_id: "bf2a4c1e-d77f-..." })
```

`bm_projects` and `bm_workspaces` themselves do **not** take routing — they list across everything.

## Recipe: writing an existing file into a specific project

When the user asks something like *"save this markdown file to my personal `main` project, return the permalink"*:

1. **Discover the project.** Call `bm_projects()` and find the entry matching the user's described project + workspace. You can route by either the workspace-qualified name (`personal/main`) or the UUID (`external_id`).

   ```
   bm_projects()
   # → [{name: "main", external_id: "bf2a4c1e-d77f-4b7a-9c3e-5d8a1f0e2b6d", workspace: "Personal", ...}, ...]
   ```

   If a project name appears in multiple workspaces, use `bm_workspaces()` to confirm which slug you want.

2. **Read the file from disk.** Use Hermes's filesystem tool (not a `bm_*` tool — local files aren't in the graph yet).

3. **Write the note with explicit routing.** Either form works; the workspace-qualified name reads cleaner in logs, the UUID is more durable.

   ```
   bm_write({
     title: "StartWithDrew Level 9 Task Queue",
     folder: "startwithdrew",
     content: <file body>,
     project: "personal/main"
   })
   # → returns "personal/main/startwithdrew/start-with-drew-level-9-task-queue"
   # (the returned permalink is workspace-qualified — carries its own routing)
   ```

4. **Verify by reading back.** No `project` arg needed — the workspace-qualified permalink routes itself.

   ```
   bm_read({ identifier: "personal/main/startwithdrew/start-with-drew-level-9-task-queue" })
   ```

Return the permalink (and the project name for clarity) to the user.

## When to use each tool

| Situation | Tool |
|---|---|
| User asks about a topic that might already be documented | `bm_search` first, then `bm_read` |
| User exposes a decision, plan, or meeting outcome | offer to `bm_write` |
| Updating prior work | `bm_edit` (append for time-ordered logs, replace_section for living docs) |
| Exploring related concepts | `bm_context` |
| "What was I working on yesterday?" / no specific query yet | `bm_recent` |
| User names a project that isn't the active one | `bm_projects` → call read/write tool with `project: "workspace/name"` or `project_id: "<uuid>"` |
| Same project name might exist in multiple workspaces | `bm_projects` (+ `bm_workspaces` if needed) → route with workspace-qualified `project` or `project_id` |
| Following up on a freshly-written note | Use the returned permalink directly — it already encodes the routing |

## Note structure

BM treats `- [category]` lines as **observations** and WikiLink lines under `## Relations` as **relations**. Categories (`[decision]`, `[insight]`, `[risk]`, `[fact]`, `[todo]`, …) and relation types (`relates_to`, `implements`, `depends_on`, `blocks`, …) are open-ended — use what fits the content. YAML frontmatter is supported with `title`, `type`, `tags`, and `permalink` as standard fields; any custom fields are allowed. See the [knowledge format docs](https://docs.basicmemory.com/raw/concepts/knowledge-format.md) for the full convention.

```markdown
# Clear Title

## Context
Background and current situation.

## Key Points
- Main insights
- Important details

## Observations
- [decision] We chose PostgreSQL for ACID guarantees
- [insight] Users prefer social login
- [risk] Deployment lacks rollback path

## Relations
- relates_to [[Other Note Title]]
- depends_on [[Database Choice]]

## Next Steps
- [ ] Implement
- [ ] Document
```

## Behavior guidelines

1. **Search before answering.** If the user asks "what did we decide about X?", run `bm_search` first.
2. **Offer to capture.** When the user shares decisions or meeting outcomes, ask: "Should I save this as a note?"
3. **Suggest connections.** When a search returns related notes, surface them so the user knows what already exists.
4. **Don't over-capture.** Auto-capture is already running per turn. Don't create a `bm_write` for every response — only for substantive content the user wants preserved.
5. **Sensitive info.** Don't capture credentials or personal data without confirmation.

## Footgun

If a note's body contains literal `<memory-context>...</memory-context>` tags, Hermes's streaming output scrubber will eat those tags (and the text between paired ones) when you echo the note verbatim back to the user. Tool *inputs* are unaffected. If you must include such content, fence it in a code block.

## Further reading

Official docs live at [docs.basicmemory.com](https://docs.basicmemory.com). Every page has an AI-friendly raw markdown view at `/raw/<path>.md` (or send `Accept: text/markdown` to the canonical URL). `WebFetch` any of these when you need detail beyond what this skill covers:

- **[Knowledge format](https://docs.basicmemory.com/raw/concepts/knowledge-format.md)** — observation categories, relation types, frontmatter conventions.
- **[Observations & relations](https://docs.basicmemory.com/raw/concepts/observations-and-relations.md)** — how notes form a graph that's searchable and traversable.
- **[Memory URLs](https://docs.basicmemory.com/raw/concepts/memory-urls.md)** — title-based addressing, wildcards (`memory://docs/*`), and routing resolution order.
- **[Projects & folders](https://docs.basicmemory.com/raw/concepts/projects-and-folders.md)** — multi-project layout, folder organization, cloud routing behavior.
- **[Semantic search](https://docs.basicmemory.com/raw/concepts/semantic-search.md)** — how `bm_search` resolves queries (semantic + full-text).
- **[MCP tools reference](https://docs.basicmemory.com/raw/reference/mcp-tools-reference.md)** — Basic Memory's full MCP surface (the `bm_*` tools here are a curated subset).
- **[Cloud routing](https://docs.basicmemory.com/raw/cloud/routing.md)** — local vs cloud project modes, per-project routing setup.
- **[llms.txt index](https://docs.basicmemory.com/llms.txt)** — full sitemap of raw markdown pages, useful when you need to look up a page not listed above.
