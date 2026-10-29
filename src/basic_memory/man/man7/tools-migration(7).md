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

Basic Memory 1.0 has one MCP tool per job, named `bm_*`. The pre-1.0 tools are removed with no
aliases. Section 3 documents each tool.

`bm tool` mirrors the local note, search, project and schema tools: `read`, `write`, `edit`,
`move`, `delete`, `list`, `find`, `search`, `context`, `project` and `schema`, with flags named
after the tool's parameters. The rest have their own commands or none: [[bm-man(3)]] is
`bm man`, [[bm-project-delete(3)]] is `bm project remove`, and the Cloud-only tools
([[bm-agent(3)]], the ChatGPT `search` and `fetch` adapters) have no `bm tool` command.

## MCP TOOLS

| Pre-1.0 tool | 1.0 replacement |
| --- | --- |
| `read_note`, `read_content`, `view_note`, `cat` | [[bm-read(3)]] |
| `write_note` | [[bm-write(3)]] |
| `edit_note` | [[bm-edit(3)]] |
| `move_note` | [[bm-move(3)]] |
| `delete_note` | [[bm-delete(3)]] |
| `list_directory`, `ls` | [[bm-list(3)]] for one folder |
| `find` | [[bm-find(3)]] |
| `search_notes`, `grep` | [[bm-search(3)]] |
| `build_context` | [[bm-context(3)]] |
| `recent_activity`, `tail` | [[bm-project(3)]] with `command="recent"` |
| `list_memory_projects`, `list_workspaces` | [[bm-project(3)]] with `command="list"` |
| `create_memory_project` | [[bm-project(3)]] with `command="create"` |
| `cloud_info` | On Basic Memory Cloud, `bm_project(command="info")`; a local server has none |
| `delete_project` | [[bm-project-delete(3)]], when enabled in config |
| `schema_validate`, `schema_infer`, `schema_diff` | [[bm-schema(3)]] |
| `man` | [[bm-man(3)]] |
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
