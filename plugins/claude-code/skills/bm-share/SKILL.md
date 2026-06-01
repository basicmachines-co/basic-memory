---
name: bm-share
description: Promote a note from your personal Basic Memory project to a shared team project, with attribution. Use when the user says "share this with the team", "publish this decision", or runs /basic-memory:bm-share. This is the deliberate way to write to a team workspace — auto-capture never does.
argument-hint: <note title or permalink to share>
---

# Share to a team project

Copy a note from the personal/primary project into a configured **team project** so
teammates can see it. This is the *only* path by which the plugin writes to a shared
project — session checkpoints and `/basic-memory:bm-remember` always stay personal.

## Steps

1. **Resolve config.** Read `.claude/settings.json` (+ `.local`) `basicMemory`:
   - `teamProjects` — a map of `<project-ref>` → `{ "promoteFolder": "shared" }`.
     These are the allowed share targets. `<project-ref>` is a workspace-qualified
     name (e.g. `my-team-2/notes`) or an `external_id` UUID.
   - `primaryProject` — the source project notes are read from.

   If `teamProjects` is empty, tell the user there's no share target configured and
   suggest adding one (or running `/basic-memory:bm-setup`), then stop. Don't invent a
   target.

2. **Find the source note.** From `$ARGUMENTS` (a title, permalink, or `memory://`
   URL), `read_note` it from `primaryProject`. If `$ARGUMENTS` is empty (you were
   invoked from "share this"), use the note most clearly referenced in the
   conversation — confirm which one if there's any ambiguity. Capture its title,
   full content (including frontmatter), and source permalink.

3. **Pick the target.** If `teamProjects` has one entry, use it. If several, ask
   which team project to share to. Use that entry's `promoteFolder` (default
   `shared`).

4. **Confirm before writing.** This write is visible to teammates, so show the user
   what you're about to do — *"Share '<title>' to <target>/<promoteFolder>?"* — and
   wait for a yes. Never share silently.

5. **Write the shared copy** with `write_note`:
   - Route to the target: if the team ref is an `external_id` UUID, pass it as
     `project_id`; otherwise pass the workspace-qualified name as `project`. (A bare
     UUID in `project` won't route — Basic Memory takes UUIDs only via `project_id`.)
   - `directory` = the `promoteFolder`
   - `title` = the source title
   - `content` = the source's content, with attribution added: keep its frontmatter
     (so a shared decision stays `type: decision` and is findable in the team's
     structured recall), add a `shared_from: <source permalink>` frontmatter field,
     and add an observation `- [context] Shared from <source permalink>`.
   Don't overwrite an existing note at the target unless the user says so.

6. **Confirm** in one line: what was shared and the new team permalink, e.g.
   `Shared → my-team-2/shared/<slug>`.

## Notes

- Sharing **copies** the note; the original stays in your project. Edits to one
  don't propagate to the other.
- Don't share notes containing secrets, credentials, or anything the user wouldn't
  want teammates to see — if in doubt, ask.
- Use whichever Basic Memory MCP server is connected; route to the team project by
  the `project` (qualified name) or `project_id` (UUID) argument.
