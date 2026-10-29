# Manual Pages

Basic Memory ships a manual in the style of Unix man pages
([#952](https://github.com/basicmachines-co/basic-memory/issues/952)). The pages are Markdown
files bundled in the package, so every install has the same manual: local, Cloud, or offline.

## Where it lives

- `src/basic_memory/man/man1/` — shell commands run against a project (`ls(1)`, `grep(1)`, ...)
  and `bm` subcommands (`okf-export(1)`, `okf-check(1)`).
- `src/basic_memory/man/man3/` — one page per MCP tool a server registers: the `bm_*` tools,
  plus the Cloud-registered `bm_agent` and ChatGPT `search`/`fetch` adapters.
- `src/basic_memory/man/man7/` — concepts an agent needs, such as `comark(7)`, and
  `tools-migration(7)`, which maps pre-1.0 tools and `bm tool` commands to 1.0.
- `src/basic_memory/man/bm.1` — the groff system page that `bm man install` copies for
  `man bm`. It is not a `bm man` page.

A test holds section 3 equal to the tool registry: every registered tool has a page, and every
section 3 page names a registered tool.

## Reading it

```
memory://man                      # the index (apropos)
memory://man/bm-search(3)         # one page
bm_man(page="bm-search(3)")       # one page, from any MCP client
bm_man(query="frontmatter filters")
bm man bm-search                  # one page, in a shell
bm man apropos WORDS              # search the bundled pages
bm man list                       # every page with its summary
bm man install                    # copy bm.1 for `man bm`
```

`bm man install` warns with a one-line `MANPATH` fix if the install root is not on the man path.

## Page anatomy

Pages use the classic headers where they apply: `NAME`, `SYNOPSIS`, `DESCRIPTION`,
`PARAMETERS`, `MCP USAGE`, `CLI EQUIVALENT`, `EXAMPLES`, `GOTCHAS`, `SEE ALSO`.

```yaml
title: bm-write(3)
type: manpage
section: 3                      # 1 | 3 | 7
name: bm-write                  # page name without section suffix
summary: create or overwrite a markdown note in the knowledge base
generated: hand                 # hand | registry | cli  (regeneration ownership)
tool: bm_write                  # section-3 pages: the MCP tool documented
command: basic-memory status    # section-1 pages: the CLI command documented
```

`SEE ALSO` entries are written as `- see_also [[page(N)]]`.

## Generated sections

`just man-regen` renders every section 3 `SYNOPSIS` and `PARAMETERS` block from the live tool
registry, and every section 1 `SYNOPSIS` and `OPTIONS` block from the Typer command tree. Drift
tests hold the shipped blocks byte-equal to the rendering, so changing a tool or a command
without regenerating fails CI. Those pages declare `generated: registry` or `generated: cli`;
everything else on a page is hand-owned.

## Adding a page

For a new MCP tool, `uv run python scripts/update_man_pages.py --new TOOL` scaffolds a section 3
page: frontmatter, `NAME`, the generated `SYNOPSIS` and `PARAMETERS`, and empty `EXAMPLES` and
`SEE ALSO` to fill in. It never overwrites an existing page. Run every example before shipping it.

When a tool is removed, remove its page in the same change, and add its replacement to
`tools-migration(7)`.

### Why "man1", "man3", "man7"?

Unix numbers manual sections: 1 for commands, 3 for library functions, 7 for overviews and
conventions. The MCP tools are the library an agent calls, so they live in section 3.
