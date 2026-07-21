---
name: bm-share
description: Share a personal Basic Memory note to a configured team project from Codex with attribution and explicit confirmation.
---

# Share A Note

Copy a note from the configured primary project to a configured team project. This
is the deliberate shared-write path. Automatic checkpoints and quick remembers
stay personal.

## Steps

1. Read `~/.codex/basic-memory.json`, then the nearest project
   `.codex/basic-memory.json`; project keys override user keys. Resolve:
   - `primaryProject`
   - `teamProjects`, a map of project ref to settings

2. If no team projects are configured, stop and ask the user to run setup or add a
   target. Do not invent a team destination.

3. Read the source note from the user's argument or the current conversation. If
   ambiguous, ask which note to share.

4. Pick the target. If there is more than one team project, ask which one.

5. Confirm before writing. The prompt should be specific:
   `Share "<title>" to <target>/<promoteFolder>?`

6. Write the copy:
   - route to the target project
   - `directory`: target `promoteFolder`, default `shared`
   - preserve the original content and useful frontmatter
   - add `shared_from: <source permalink>` frontmatter when possible
   - add `- [context] Shared from <source permalink>` as an observation

7. Confirm with the new team permalink.

Never share secrets, credentials, or private notes without an explicit yes.
