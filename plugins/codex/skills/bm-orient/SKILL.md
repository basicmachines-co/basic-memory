---
name: bm-orient
description: Orient Codex from Basic Memory before substantial repo work by reading active tasks, decisions, recent Codex checkpoints, and repo conventions.
---

# Orient From Basic Memory

Use this before substantial work in a repo, before resuming an old thread, or when
the user asks where things stand.

## Steps

1. Read `~/.codex/basic-memory.json`, then the nearest project
   `.codex/basic-memory.json`; project keys override user keys. Use `primaryProject`, `secondaryProjects`,
   `recallTimeframe`, `sessionProfile`, `repository`, and `placementConventions`.
   If the file is missing, continue
   against the default Basic Memory project and mention that setup has not been run.

2. Query the primary project:
   - active tasks: `type=task`, `status=active`
   - open decisions: `type=decision`, `status=open`
   - recent Codex sessions: `type=codex_session`, after `recallTimeframe`
   - recent coding sessions: `type=coding_session`,
     `repository=<configured repository>`, after `recallTimeframe`, when
     `sessionProfile=coding`
   - recent core-projected sessions: `type=session`, after `recallTimeframe`

   Always query `codex_session` and `session`; include `coding_session` for a
   coding profile only with the configured `repository` metadata filter. Never
   run an unscoped coding-session query; if the repository is missing, report
   that setup is incomplete. Merge and deduplicate the results, sort them
   newest first, and prefer the highest-signal checkpoint regardless of which
   producer wrote it. `coding_session` carries schema-required, queryable Git
   context; `codex_session` preserves general and legacy Codex checkpoints;
   `session` carries normalized artifacts from `bm hook flush`.

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
