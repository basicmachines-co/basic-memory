---
name: memory-doctor
description: "Check local Basic Memory projects with bm doctor, explain the findings, and run the suggested fixes once the user agrees. Use when search misses or repeats notes, or a save looks lost."
---

# Memory Doctor

`bm doctor` checks the user's local Basic Memory projects and reports what is
wrong. Each problem comes with the command that fixes it, when one exists.
Doctor's checks only read; they never change a project's notes or index. The
fixes it suggests do change things, so run them only after the user agrees.

This skill needs a shell with the `bm` (or `basic-memory`) command.

## When to Use

- Search misses a note the user knows exists, or returns the same note twice
- A note was saved but its file did not change, or the reverse
- The user asks whether Basic Memory is working, or to "check" or "fix" it
- After a crash, an interrupted import, or moving a project's folder

## Run It

Start with the report alone. It is fast and has a stable JSON shape:

```bash
bm doctor --skip-self-test --json
```

Add `--project NAME` to check one project. Without `--skip-self-test`, doctor
also runs a self-test: it writes, indexes and searches a note in a throwaway
sandbox, which takes a few seconds and proves the install itself works. Run it
when the report is clean but something still seems broken.

## Read the Report

```json
{
  "projects": [
    {
      "name": "work",
      "findings": [
        {
          "check": "search_index",
          "severity": "error",
          "summary": "1 search row does not match a current note, observation or relation.",
          "evidence": ["entity id=99, entity_id=99"],
          "action": {"kind": "reindex_search", "full": true},
          "command": "bm reindex --search --full --project work"
        }
      ],
      "has_errors": true
    }
  ],
  "skipped_cloud_projects": ["research"],
  "self_test": {"outcome": "skipped"},
  "has_errors": true
}
```

| Severity | Meaning | What to do |
|---|---|---|
| `error` | Will not go away without a fix | Offer the `command`; if it is null, explain the evidence |
| `warning` | Needs attention, may recover on its own | Offer the `command` if there is one |
| `info` | Expected while work is in progress | Mention it; a running server finishes it, and the `command` finishes it now |

Indexing catches up in the background. A duplicate, missing or extra search row
is a `warning` while file indexing is still running, because the pass may clear
it, and becomes an `error` once indexing has settled. If the report has only `info` findings, nothing is wrong. When no
Basic Memory server is running, nothing indexes in the background, so offer the
`command` on an `info` finding too.

Cloud projects are listed under `skipped_cloud_projects`: they are indexed on the
server, and local commands cannot fix them.

## Fix

1. Summarize the findings for the user in plain words: which project, what is
   wrong, and what the fix does. Group findings that share a command.
2. Show each `command` exactly as doctor printed it and ask before running it.
   `bm reindex --search --full` re-reads every file in the project and can take
   a while on a large one.
3. Run the commands the user approves.
4. Run `bm doctor --skip-self-test --json` again and confirm the findings are gone.

When a finding has no `command`, do not invent one. Say what the evidence shows
(for example, which files changed on disk while a newer save was waiting) and
let the user decide.

## Exit Codes

`bm doctor` exits 0 when there are no errors and the self-test passed (or was
skipped), and 1 otherwise. Warnings and info never fail it.
