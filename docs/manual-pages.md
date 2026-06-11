# Manual Pages

Basic Memory's manual is written in the style of Unix man pages — and
implemented as Basic Memory notes ([#952](https://github.com/basicmachines-co/basic-memory/issues/952)).
Every page is a markdown note conforming to the `Manpage` schema, `SEE ALSO`
entries are real knowledge-graph relations, and every example on every page
was executed against a live project before the page shipped. The manual
documents the tools; the tools verify the manual.

## Where it lives

The canonical manual is the **`manual` project in the Basic Memory team
workspace** (cloud, shared). Anyone can build their own: the schema ships as
an opt-in seed at `plugins/claude-code/schemas/manpage.md` — copy it into any
project's folder and start writing pages against it.

Layout:

```
manual/
├── schemas/Manpage.md      # the manpage schema (type: schema)
├── man1/                   # CLI commands        bm(1), bm-status(1), ...
├── man3/                   # MCP tools           write-note(3), search-notes(3), ...
├── man5/                   # file formats        bm-note(5), bm-observation(5), ...
├── man7/                   # concepts            basic-memory(7), semantic-memory(7), ...
├── playground/             # scratch notes for destructive examples
└── diagrams/               # canvas visualizations of the manual graph
```

Sections follow Unix numbering: `1` CLI commands, `3` MCP tools, `5` file
formats and schemas, `7` concepts, `8` admin/cloud operations.

## Page anatomy

Pages use the classic headers where applicable: `NAME`, `SYNOPSIS`,
`DESCRIPTION`, `PARAMETERS`, `MCP USAGE`, `CLI EQUIVALENT`, `EXAMPLES`,
`GOTCHAS`, `SEE ALSO`. Frontmatter (validated by the schema):

```yaml
type: manpage
section: 3                      # 1 | 3 | 5 | 7 | 8
name: write-note                # page name without section suffix
summary: create or overwrite a markdown note in the knowledge base
generated: hand                 # hand | registry | typer  (regeneration ownership)
tool: write_note                # section-3 pages: the MCP tool documented
command: basic-memory status    # section-1 pages: the CLI command documented
verified: 0.21.6 mcp+cli        # version + path(s) that proved the page
```

Field knowledge accumulates as observations — `[gotcha]`, `[bug]` (with issue
links), `[pattern]` — and `SEE ALSO` entries are `see_also` relations, so the
manual is a navigable graph, not a folder of files.

## How to use it

Man-style reads (any MCP client or the CLI):

```bash
# read a page
bm tool read-note "man3/write-note-3" --project manual

# apropos — find pages by section, tool, or text
bm tool search-notes --project manual          # then filter, or via MCP:
#   search_notes(project="manual", metadata_filters={"type": "manpage", "section": 3})
#   search_notes(project="manual", metadata_filters={"type": "manpage", "tool": "write_note"})

# traverse SEE ALSO from any page
#   build_context(url="man3/write-note-3", project="manual")
```

A future `bm man <topic>` command is thin sugar over exactly these calls.

## The verification discipline

Two rules make the manual trustworthy:

1. **Examples must have run.** An `EXAMPLES` (or `MCP USAGE` / `CLI
   EQUIVALENT`) block contains only commands that actually executed against
   the manual project. Destructive operations (`delete_note`, `move_note`,
   destructive `edit_note`) run only against `playground/` notes — never
   against pages. The `verified:` field records the version and which path
   proved the page: `mcp` (live service), `cli` (dev checkout), or both.

2. **The schema is the linter.** Validate the whole manual any time:

   ```bash
   bm tool schema-validate manpage --project manual
   # → {"total_notes": 38, "valid_count": 38, "warning_count": 0, ...}
   ```

   `bm orphans --project manual` confirms every page is connected to the
   graph, and `schema_diff`/`schema_infer` report drift between the schema
   and how pages are actually written.

Because verification exercises real tool calls against the live service,
building the manual doubles as an end-to-end smoke test. The initial build
found six bugs in one pass (#954–#959) — including the verification rule
catching a test that asserted a bug as expected output (#958).

## Adding or updating a page

1. Run the commands you intend to document; keep the actual output.
2. Write the page with `write_note`, passing frontmatter through the
   `metadata` parameter (nested YAML in content frontmatter is unreliable on
   some clients):

   ```
   write_note(title="my-tool(3)", directory="man3", project="manual",
              note_type="manpage",
              metadata={"section": 3, "name": "my-tool",
                        "summary": "...", "generated": "hand",
                        "tool": "my_tool", "verified": "<version> mcp"})
   ```

3. Link related pages in `SEE ALSO` with `see_also [[other-page(3)]]`.
   Forward references to pages that don't exist yet are fine — they resolve
   automatically when the target is written.
4. Validate: `bm tool schema-validate manpage --project manual`.

For mechanical updates to generated sections, prefer `edit_note` with
`replace_section` / `insert_after_section` so curated content (EXAMPLES,
GOTCHAS, SEE ALSO, observations) survives — that ownership split is what the
`generated:` field declares.

## Roadmap

- **Registry generator** — section-3 SYNOPSIS/PARAMETERS generated from the
  MCP tool registry (docstrings + pydantic schemas), section-1 from Typer
  help; the hand-written corpus is the template spec. Regenerate-and-diff in
  CI becomes the drift gate.
- **`bm man <topic>`** — CLI sugar over `read_note` + metadata search.
- **Real man pages / docs site** — the same extraction renders to groff
  ([#610](https://github.com/basicmachines-co/basic-memory/issues/610)) and
  to the hosted docs site; the notes remain canonical for sections 5 and 7,
  code is canonical for 1 and 3.
