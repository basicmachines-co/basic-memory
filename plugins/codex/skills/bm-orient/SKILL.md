---
name: bm-orient
description: Orient Codex from Basic Memory before substantial repo work by reading active tasks, decisions, recent Codex checkpoints, and repo conventions.
---

# Orient From Basic Memory

Use this before substantial work in a repo, before resuming an old thread, or when
the user asks where things stand.

## Steps

1. Read `.codex/basic-memory.json` if present. Use `primaryProject`, `secondaryProjects`,
   `recallTimeframe`, and `placementConventions`. If the file is missing, continue
   against the default Basic Memory project and mention that setup has not been run.

2. Query the primary project:
   - active tasks: `type=task`, `status=active`
   - open decisions: `type=decision`, `status=open`
   - recent Codex sessions: `type=codex_session`, after `recallTimeframe`
   - recent generic sessions only if no Codex sessions are found

3. Query configured `secondaryProjects` read-only for open decisions. Do not write
   to shared projects during orientation.

4. Read the highest-signal hits before summarizing. Prefer notes that match the
   current repo, named route, issue, branch, or file path.

5. Present a compact orientation:
   - active work
   - decisions that constrain the next move
   - recent checkpoint cursor
   - likely next action
   - any missing setup or ambiguous project mapping

Keep the summary evidence-backed. Include permalinks for notes you rely on.
