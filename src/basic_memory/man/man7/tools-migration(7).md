---
title: tools-migration(7)
type: manpage
section: 7
name: tools-migration
summary: map pre-1.0 and 1.0 MCP tools and bm tool commands to their replacements
generated: hand
---

# tools-migration(7)

## NAME

**tools-migration** — map pre-1.0 and 1.0 MCP tools and `bm tool` commands to their replacements

## DESCRIPTION

Basic Memory 1.0 has one MCP tool per job, named `bm_*`. The pre-1.0 tools in the table below
are removed with no aliases. The ChatGPT connector's `search` and `fetch` tools are not retired:
Basic Memory Cloud still registers them. Section 3 documents each tool.

`bm tool` mirrors the local note, search, project and schema tools: `read`, `write`, `edit`,
`move`, `delete`, `list`, `find`, `search`, `context`, `project-list`, `project-create`,
`recent`, `schema-validate`, `schema-infer` and `schema-diff`, with flags named after the
tool's parameters. The rest have their own commands or none: [[bm-man(3)]] is
`bm man`, [[bm-project-delete(3)]] is `bm project remove`, and the Cloud-only tools
(the agent tools in [[agents(7)]], the ChatGPT `search` and `fetch` adapters) have no `bm tool` command.

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
| `recent_activity` | `bm_recent` |
| `tail` | `bm_recent`; map `timeframe` to `since` and `lines` to `page_size`. Recent JSON includes `created_at`, not `updated_at`. The `bm tail` shell command is removed; use `bm tool recent`. |
| `list_memory_projects` | `bm_project_list` |
| `list_workspaces` | `bm_project_list` groups projects by workspace, so it omits a workspace with no projects; `bm cloud workspace list` in a shell lists every workspace |
| `create_memory_project` | `bm_project_create(name=..., path=...)` |
| `delete_project` | `bm_project_delete` (opt-in) |
| `schema_validate`, `schema_infer`, `schema_diff` | `bm_schema_validate`, `bm_schema_infer`, `bm_schema_diff` |
| `move_note` | `bm_move` |
| `delete_note` | `bm_delete` |
| `man` | `bm_man` |

Two more retired tools: `cloud_info` is `bm_cloud_setup_status` (setup status) and
`bm_workspace_usage` (workspace usage) on Basic Memory Cloud, with no local replacement, and `basic_memory_diagnostics` is `bm doctor` and `bm config list` in a shell.

## 1.1 CHANGES

Basic Memory 1.1 splits the 1.0 tools that chose an operation with a `command` argument into one
tool per operation, so each tool declares its own required parameters and read-only or
destructive hints. The 1.0 names are removed with no aliases.

| 1.0 call | 1.1 replacement |
| --- | --- |
| `bm_project(command="list")` | `bm_project_list()` |
| `bm_project(command="create", name=..., path=...)` | `bm_project_create(name=..., path=...)` |
| `bm_project(command="recent", project=...)` | `bm_recent(project=...)` |
| `bm_schema(command="validate", ...)` | `bm_schema_validate(...)` |
| `bm_schema(command="infer", note_type=...)` | `bm_schema_infer(note_type=...)` |
| `bm_schema(command="diff", note_type=...)` | `bm_schema_diff(note_type=...)` |
| `bm tool project list` | `bm tool project-list` |
| `bm tool project create --name N --path P` | `bm tool project-create --name N --path P` |
| `bm tool project recent` | `bm tool recent` |
| `bm tool schema validate/infer/diff --note-type T` | `bm tool schema-validate/schema-infer/schema-diff --note-type T` |

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
| `recent-activity`, `recent` | `recent` |
| `recent --timeframe D --type K --depth N` | `recent --since D --items K`; depth is fixed at 1 |
| `list-projects`, `projects` | `project-list` |
| `list-workspaces`, `workspaces` | `project-list` (omits workspaces with no projects), or `bm cloud workspace list` for every workspace |
| `schema-validate`, `schema-infer`, `schema-diff`, `validate`, `infer`, `diff` | `schema-validate`, `schema-infer`, `schema-diff`; a positional `TYPE` becomes `--note-type TYPE` |
| `validate path/note.md` | `schema-validate --identifier path/note.md` |
| `--project-id UUID` | `--project UUID` |
| `--plain` | removed; the default output is the tool's text result |
| (new) | `move`, `list`, `find` |

`bm tool` prints the tool's text result, or its JSON result with `--json`. It no longer
switches to JSON when piped, so scripts pass `--json`.

## SHELL READ VERBS

The shell read verbs now run the same tools the same way: `bm cat` and `bm head` call
`bm_read`, `bm grep` calls `bm_search` in text mode, `bm ls` calls `bm_list`, and
`bm find` and `bm tree` call `bm_find`. Their output is the tool's text result, or its
JSON result with `--json`.

| Previous shell verb | 1.0 shell verb |
| --- | --- |
| `bm tail` | removed; use `bm tool recent` |
| `--plain`, Rich tables, JSON when piped | removed; text result by default, `--json` for JSON |
| `--project-id UUID` | `--project UUID` |
| no `--project` with several projects: refused | the configured default project |
| `<project>/path` routes to that project by itself | pass `--project <project>`; a prefix naming another project is refused |
| `bm ls /` lists the projects | lists the default project's root; `bm project list` lists projects |
| `bm cat --lines` with `--no-frontmatter` counts body lines | `--lines` always counts the whole file, frontmatter included; `--frontmatter` applies to whole-note reads |
| `bm head --frontmatter/--no-frontmatter` | removed; `head` reads lines 1-N of the file |
| `bm grep -C/--context-lines N` | `bm grep -C/--lines N` |
| `bm grep ""` refused; `-C` with `--semantic` refused | follow `bm_search` |
| (new) `bm grep --path DIR` | search one folder |
| (new) `bm ls --glob`, `--sort` | `bm_list`'s file-name glob and order |
| `ls`/`find`/`tree` `--page-size` up to 200 | 1-100; `tree` defaults to 100 |
| `bm find --fields` hit rows rendered as a table | `bm_find`'s text result |

## SEE ALSO

- see_also [[bm-man(3)]]
