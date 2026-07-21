---
name: bm-orient
description: Orient Claude from Basic Memory before substantial repo work by reading active tasks, open decisions, recent checkpoints, and repo conventions. Use when resuming old work or when the user asks where things stand.
argument-hint: (no arguments — reads the graph and summarizes)
---

# Orient From Basic Memory

Use this before substantial work in a repo, before resuming an old thread, or when
the user asks where things stand. The SessionStart hook already briefs the start
of a session; this is the deliberate mid-session version with deeper reads.

## Steps

1. Resolve config: read the `basicMemory` block with the same precedence the
   hooks use — user-level `~/.claude/settings.json` as the base, then the
   project's `.claude/settings.json` and `.claude/settings.local.json` override
   it per key. Use `primaryProject`, `secondaryProjects`, `recallTimeframe`,
   `sessionProfile`, `repository`, and `placementConventions`. If no config is
   present, continue against the default Basic Memory project and mention that
   setup has not been run. Scope queries to `primaryProject` by passing it as
   `project`, or as `project_id` if it's an `external_id` UUID.

2. Query the primary project with `search_notes`:
   - active tasks: `metadata_filters={"type": "task", "status": "active"}`
   - open decisions: `metadata_filters={"type": "decision", "status": "open"}`
   - recent sessions: `metadata_filters={"type": "session"}`, after
     `recallTimeframe`
   - recent coding sessions: `metadata_filters={"type": "coding_session",
     "repository": "<configured repository>"}`, after `recallTimeframe`, when
     `sessionProfile` is `coding`

   Always query `"type": "session"`; include `"type": "coding_session"` for a
   coding profile only with the configured `repository` metadata filter.
   Never run an unscoped coding-session query; if the repository is missing,
   report that setup is incomplete. Merge and deduplicate the results, sort them
   newest first, and prefer the highest-signal checkpoint regardless of which
   producer wrote it. `coding_session` carries schema-required, queryable Git
   context; `session` covers general checkpoints, PreCompact captures, and
   normalized `bm hook flush` projections.

3. Query configured `secondaryProjects` read-only for open decisions. Do not write
   to shared projects during orientation.

4. Read the highest-signal hits before summarizing. Prefer notes that match the
   current repository, branch, Git SHA, pull request, named route, issue, or file
   path. For coding sessions, use structured metadata filters before text search.

5. Present a compact orientation:
   - active work
   - decisions that constrain the next move
   - recent checkpoint cursor
   - likely next action
   - any missing setup or ambiguous project mapping

Keep the summary evidence-backed. Include permalinks for notes you rely on.
