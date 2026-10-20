# bm doctor

`bm doctor` reports problems in your local Basic Memory projects and, for each one
it can fix, the command that fixes it. It only reads: it never changes a project,
your config, or the database. See #1798 for the design.

```bash
bm doctor                      # every local project, then the sandboxed self-test
bm doctor --project work       # one project
bm doctor --skip-self-test     # report only; fast
bm doctor --json               # the same report for scripts and agents
```

It exits 0 when no finding is an error and the self-test passed, and 1 otherwise.
The `memory-doctor` skill teaches an agent to run it, explain the findings, and
run the fixes once the user agrees.

## Checks

| Check | Finds | Fix |
|---|---|---|
| `project_path` | The project directory no longer exists | Restore it, or point the project at its new location |
| `index_readiness` | A project that was never indexed, or indexing still catching up (new, changed or deleted files not yet indexed) | `bm project index NAME` |
| `search_index` | Duplicate search rows, rows that match no current note, notes with no search row | `bm reindex --search --full --project NAME` |
| `materialization` | Accepted note writes that failed to save, or that were held back because the file changed on disk | `bm reindex --search --project NAME` retries failed saves |

## Severity

Search rows, embeddings and note saves are derived state that catches up in the
background, so doctor grades findings by whether they will go away on their own:

- **info**: expected while work is in progress, such as indexing still catching
  up. No action.
- **warning**: needs attention but may recover, such as a failed save that the
  next start retries.
- **error**: will not go away without the fix, such as a duplicated search row,
  or a missing or orphaned row once indexing has settled.

Duplicate rows are always errors: SQLite FTS5 enforces no uniqueness, and nothing
but a full reindex rewrites them. A missing or orphaned row is info while file
indexing still has work owed, and an error after it settles.

Search rows normally outnumber notes, because observations and relations have
their own rows. An unresolved relation target is valid and is not an orphan.
Notes whose modified time is before their created time are reported as info:
without frontmatter dates the parser uses file times, and copying a vault with
preserved mtimes produces exactly that.

## Self-test

The self-test creates a project, writes a note through the API, writes another
file by hand, indexes, searches and checks status. It runs in a child process
with its own temporary home, config, database and temp directory, and every
`BASIC_MEMORY_*` variable removed, so it cannot touch your install. Only the
FastEmbed model cache is shared, so the model is not downloaded again.

## API

`GET /v2/projects/{project_external_id}/doctor` returns the report for one
project: a list of findings, each with a `check`, `severity`, `summary`,
`evidence`, and an optional typed `action`. The CLI turns actions into commands;
the API does not, because a cloud project cannot run a local command.
`ProjectClient.get_doctor_report()` calls it.

Cloud projects are skipped for now and listed in the output.
