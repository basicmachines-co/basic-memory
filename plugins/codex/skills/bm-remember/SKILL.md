---
name: bm-remember
description: Quickly save a small fact, reminder, or user preference into Basic Memory from Codex without turning it into a full decision or checkpoint.
---

# Remember

Use this for lightweight capture: "remember that", "save this", "note this", or
a small fact that should survive the current thread.

## Steps

1. Read `.codex/basic-memory.json` if present:
   - `primaryProject`, default omitted
   - `rememberFolder`, default `codex-remember`

2. Identify the exact text to save. If the user supplied text, preserve their
   wording. If the user said "remember that" and the referent is unclear, ask one
   short question.

3. Write with `write_note`:
   - `title`: first line trimmed to 80 characters, or a short descriptive title
   - `directory`: `rememberFolder`
   - `content`: the text to remember
   - `tags`: `["codex", "manual-capture"]`
   - route to `primaryProject` if configured

4. Confirm in one line with the permalink.

Do not use this for decisions with alternatives or for work handoffs. Use
`bm-decide` or `bm-checkpoint` for those.
