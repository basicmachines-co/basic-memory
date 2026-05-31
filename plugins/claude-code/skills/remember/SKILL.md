---
name: remember
description: Quickly capture a thought, fact, or reminder into Basic Memory as a lightweight note. Use when the user says "remember that…", "note this", "save this to memory", or runs /basic-memory:remember. For quick deliberate capture — not full decision or session records.
argument-hint: <text to remember>
---

# Remember

Capture `$ARGUMENTS` into Basic Memory as a quick note, keeping the user's words.

## Steps

1. **Resolve config.** Read `.claude/settings.json` (and `.claude/settings.local.json`
   if it exists) and look for the `basicMemory` block:
   - `rememberFolder` — folder for quick captures (default: `bm-remember`)
   - `primaryProject` — project to write to (default: omit the `project` argument so
     Basic Memory uses its default project)

   Both are optional. Use the defaults if the block or a key is missing. Don't fail
   if there's no settings file.

2. **Derive the note.**
   - **Content** = the text in `$ARGUMENTS`, verbatim. Don't rewrite or pad it.
   - **Title** = the first line of that text, trimmed to ≤ 80 characters (append `…`
     if you truncate). If it's one long line, write a short descriptive title instead.
   - If `$ARGUMENTS` is empty (e.g. you were invoked because the user said "remember
     that…"), capture the specific thing they asked you to remember from the
     conversation. If it's genuinely unclear what to save, ask one short question.

3. **Write it** with `write_note`:
   - `title` = the derived title
   - `directory` = the resolved `rememberFolder`
   - `content` = the text
   - `tags` = `["manual-capture"]`
   - Route to `primaryProject` if set: pass it as `project`, or as `project_id` if
     it's an `external_id` UUID (a bare UUID in `project` won't route). Omit both if
     `primaryProject` isn't set.
   Don't overwrite an existing note unless the user explicitly asks.

4. **Confirm** in one line: what was saved and its permalink —
   e.g. `Saved → bm-remember/<slug>`.

## Notes

- This is a *quick* capture. Keep the user's wording; don't add observations,
  relations, or structure unless they ask.
- For a decision with rationale and alternatives, write a `type: decision` note
  instead (the basic-memory output style covers this). Wrapping up a work session is
  the PreCompact checkpoint's job, not this skill's.
- Use whichever Basic Memory MCP server is connected — don't assume a specific tool
  name prefix.
