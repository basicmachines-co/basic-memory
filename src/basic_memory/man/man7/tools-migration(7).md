---
title: tools-migration(7)
type: manpage
section: 7
name: tools-migration
summary: map pre-1.0 MCP tools and bm tool commands to their 1.0 replacements
generated: hand
---

# tools-migration(7)

## NAME

**tools-migration** — map pre-1.0 MCP tools and `bm tool` commands to their 1.0 replacements

## DESCRIPTION

Basic Memory 1.0 has one MCP tool per job, named `bm_*`. The pre-1.0 tools in the table below
are removed with no aliases. The ChatGPT connector's `search` and `fetch` tools are not retired:
Basic Memory Cloud still registers them. Section 3 documents each tool.

`bm tool` mirrors the local note, search, project and schema tools: `read`, `write`, `edit`,
`move`, `delete`, `list`, `find`, `search`, `context`, `project` and `schema`, with flags named
after the tool's parameters. The rest have their own commands or none: [[bm-man(3)]] is
`bm man`, [[bm-project-delete(3)]] is `bm project remove`, and the Cloud-only tools
([[bm-agent(3)]], the ChatGPT `search` and `fetch` adapters) have no `bm tool` command.

## MCP TOOLS

These tables are copied from the 1.0 migration guide (`docs/1.0.md`), and a test keeps them equal.
The guide's "Tool-by-tool change list" gives each tool's renamed arguments and response changes.

| Previous MCP tool | 1.0 replacement |
| --- | --- |
| `read_note`, `read_content`, `view_note`, `cat` | `bm_read` with explicit line bounds where needed |
| `write_note` | `bm_write` |
| `edit_note` | `bm_edit` |
| `search_notes`, `grep` | `bm_search` |
| `build_context` | `bm_context` |
| `list_directory`, `ls` | `bm_list` for one level; `bm_find` for recursive inventory |
| `find` | `bm_find` |
| `recent_activity` | `bm_project(command="recent")` |
| `tail` | `bm_project(command="recent")`; map `timeframe` to `since` and `lines` to `page_size`. Recent JSON includes `created_at`, not `updated_at`; shell users needing the last-change timestamp can use `bm tail --json`. |
| `list_memory_projects` | `bm_project(command="list")` |
| `list_workspaces` | `bm_project(command="list")` groups projects by workspace, so it omits a workspace with no projects; `bm cloud workspace list` in a shell lists every workspace |
| `create_memory_project` | `bm_project(command="create", name=..., path=...)` |
| `delete_project` | `bm_project_delete` (opt-in) |
| `schema_validate`, `schema_infer`, `schema_diff` | `bm_schema(command="validate"/"infer"/"diff")` |
| `move_note` | `bm_move` |
| `delete_note` | `bm_delete` |
| `man` | `bm_man` |

Two more retired tools: `cloud_info` is `bm_project(command="info")` on Basic Memory Cloud, with no
local replacement, and `basic_memory_diagnostics` is `bm doctor` and `bm config list` in a shell.

## BM TOOL COMMANDS

| Previous CLI | 1.0 CLI |
| --- | --- |
| `write-note`, `write --title T --folder F --type X` | `write --title T --directory F --note-type X` (or `--identifier path.md`) |
| `read-note`, `read --frontmatter` | `read` (frontmatter is now included by default) |
| `read` (body only, the old default) | `read --no-include-frontmatter` |
| `edit-note`, `edit --operation OP` | `edit --command OP` |
| `delete-note`, `delete ID --is-directory` | `delete --identifier ID`, or `delete --path DIR --recursive` |
| `search-notes`, `search --vector/--hybrid/--title/--permalink` | `search --mode semantic/hybrid/title/permalink` |
| `search --meta k=v`, `--filter JSON`, `--tag`, `--status` | `search --filters JSON` |
| `search --type T`, `--entity-type K`, `--after_date D` | `search --note-types T`, `--items K`, `--since D` |
| `search --category C` | `search --categories C` |
| `build-context`, `context URL --timeframe D` | `context IDENTIFIER --since D` |
| `recent-activity`, `recent` | `project recent` |
| `recent --timeframe D --type K --depth N` | `project recent --since D --items K`; depth is fixed at 1 |
| `list-projects`, `projects` | `project list` |
| `list-workspaces`, `workspaces` | `project list` (omits workspaces with no projects), or `bm cloud workspace list` for every workspace |
| `schema-validate`, `schema-infer`, `schema-diff`, `validate`, `infer`, `diff` | `schema validate/infer/diff`; a positional `TYPE` becomes `--note-type TYPE` |
| `validate path/note.md` | `schema validate --identifier path/note.md` |
| `--project-id UUID` | `--project UUID` |
| `--plain` | removed; the default output is the tool's text result |
| (new) | `move`, `list`, `find` |

`bm tool` prints the tool's text result, or its JSON result with `--json`. It no longer
switches to JSON when piped, so scripts pass `--json`.

## SEE ALSO

- see_also [[bm-man(3)]]
