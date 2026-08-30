---
title: edit-note(3)
type: manpage
section: 3
name: edit-note
summary: 'edit a note in place: append, prepend, find/replace, or section surgery'
generated: registry
tool: edit_note
verified: 0.21.6 mcp+cli
---

# edit-note(3)

## NAME

**edit-note** — edit a note in place: append, prepend, find/replace, or section surgery

## SYNOPSIS

MCP:

```
edit_note(identifier, operation, content, project=None, workspace=None,
          project_id=None, section=None, find_text=None,
          expected_replacements=None, replace_subsections=None,
          metadata=None, output_format="text")
```

CLI:

```
bm tool edit-note IDENTIFIER --operation OP --content TEXT
                  [--find-text TEXT] [--section "## Heading"]
                  [--expected-replacements N] [--project NAME]
```

## DESCRIPTION

Modifies an existing note without rewriting the whole file. Six operations:

- **append** / **prepend** — add content at the end or start; both create
  the note if it does not exist
- **find_replace** — replace occurrences of `find_text` with `content`;
  optionally validated by `expected_replacements`
- **replace_section** — replace everything under a markdown heading
- **insert_before_section** / **insert_after_section** — insert content
  around a heading without consuming it

`replace_section` is the mechanism this manual uses for regeneration:
generator-owned sections can be rewritten while curated sections survive
(see [[Manpage]]).

Unlike [[read-note(3)]], the identifier must be an **exact** title,
permalink, or memory:// URL — there is no fuzzy fallback for edits.

## PARAMETERS

- **identifier** — exact title, permalink, or memory:// URL (CLI: positional)
- **operation** — one of the six operations above
- **content** — the content to add or substitute
- **section** — heading for the section operations (e.g. `"## Observations"`)
- **find_text** — target text for find_replace
- **expected_replacements** — if set, the edit fails unless the occurrence
  count matches exactly
- **replace_subsections** — for replace_section. Default (true): the section
  runs to the next heading of the same or higher level, so replacing
  `## Section` replaces its `###` subsections too. `False` stops at the next
  heading of any level and preserves subsections
- **metadata** — dict of frontmatter fields merged in alongside any operation;
  given keys overwrite or add, other keys and the body are untouched.
  `title`, `type`, and `permalink` are ignored; keys cannot be deleted
- **project** / **project_id** / **workspace** — routing; same semantics as
  [[write-note(3)]]

## MCP USAGE

All verified against playground/ notes:

```
edit_note("playground/demo-cli-stdin", "append",
          "\n## Appended Section\n\n- [example] ... #edit",
          project="manual")
# → operation: "append", fileCreated: false

edit_note("playground/demo-pour-over-method", "replace_section",
          "- [method] ...\n- [example] regenerated #edit\n",
          section="## Observations", project="manual")
# → operation: "replace_section"

edit_note("playground/demo-pour-over-method", "find_replace", "96°C",
          find_text="205°F", expected_replacements=1, project="manual")
# → operation: "find_replace"
```

## CLI EQUIVALENT

```
bm tool edit-note playground/demo-cli-stdin \
    --operation append --content "more" --project manual
```

## EXAMPLES

Replacement-count validation fails fast and changes nothing:

```
edit_note("playground/demo-cli-stdin", "find_replace", "standard input",
          find_text="stdin", expected_replacements=99, project="manual")
# → error: "Expected 99 occurrences of 'stdin', but found 4"
```

## GOTCHAS

- [gotcha] find_replace searches the whole file including YAML frontmatter — title and permalink fields can be silently rewritten if your find_text matches them; count occurrences with expected_replacements to guard #frontmatter
- [gotcha] The CLI defaults --expected-replacements to 1, but the MCP tool defaults to no validation at all — the same edit can fail via CLI and succeed via MCP #cli-parity
- [gotcha] append and prepend create missing notes instead of erroring; the other four operations require the note to exist #semantics
- [gotcha] edit_note accepts a workspace parameter that most sibling tools lack — prefer project_id for unambiguous cross-workspace routing #routing
- [pattern] Use expected_replacements on every scripted find_replace; it converts silent over-replacement into a loud failure #safety

## SEE ALSO

- see_also [[write-note(3)]]
- see_also [[read-note(3)]]
- see_also [[move-note(3)]]
- see_also [[delete-note(3)]]
