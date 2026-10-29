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

This table matches the 1.0 migration guide (`docs/1.0.md`), whose "Tool-by-tool change list"
gives each tool's renamed arguments and response changes.

| Pre-1.0 tool | 1.0 replacement |
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
| `list_memory_projects`, `list_workspaces` | `bm_project(command="list")` |
| `create_memory_project` | `bm_project(command="create", name=..., path=...)` |
| `delete_project` | `bm_project_delete` (opt-in) |
| `schema_validate`, `schema_infer`, `schema_diff` | `bm_schema(command="validate"/"infer"/"diff")` |
| `move_note` | `bm_move` |
| `delete_note` | `bm_delete` |
| `man` | `bm_man` |
| `cloud_info` | On Basic Memory Cloud, `bm_project(command="info")`; a local server has none |
| `basic_memory_diagnostics` | `bm doctor` and `bm config list` from the shell |

## BM TOOL COMMANDS

| Pre-1.0 | 1.0 |
| --- | --- |
| `write-note`, `read-note`, `edit-note`, `delete-note` | `write`, `read`, `edit`, `delete` |
| `search-notes`, `build-context` | `search`, `context` |
| `recent-activity`, `recent` | `project recent` |
| `list-projects`, `projects`, `list-workspaces`, `workspaces` | `project list` |
| `schema-validate`, `schema-infer`, `schema-diff`, `validate`, `infer`, `diff` | `schema validate`, `schema infer`, `schema diff` |
| `write --folder F --type X` | `write --directory F --note-type X`, or `--identifier path.md` |
| `edit --operation OP` | `edit --command OP` |
| `delete ID --is-directory` | `delete --identifier ID`, or `delete --path DIR --recursive` |
| `search --vector`, `--hybrid`, `--title`, `--permalink` | `search --mode semantic`, `hybrid`, `title`, `permalink` |
| `search --meta k=v`, `--filter`, `--tag`, `--status` | `search --filters JSON` |
| `search --type`, `--entity-type`, `--after_date` | `search --note-types`, `--items`, `--since` |
| `context URL --timeframe D` | `context IDENTIFIER --since D` |
| `--project-id UUID` | `--project UUID` |

`bm tool` prints the tool's text result, or its JSON result with `--json`. It no longer
switches to JSON when piped, so scripts pass `--json`.

## SEE ALSO

- see_also [[bm-man(3)]]
