---
name: memory-comark
description: "Write Basic Memory notes that render as charts, tables, boards, and live dashboards with `::bm-*` Comark components. Use when a note would read better as a figure, checklist, kanban board, or a live view over the knowledge base (counts, rankings, recent activity, search results), or when editing a note that already contains `::bm-*` directives."
---

# Memory Comark

Basic Memory notes can carry small, safe components written as Comark block
directives (`::bm-bars{...}` … `::`). Basic Memory Cloud draws them in Notes, the
Wiki, and shared pages. Everywhere else (a local editor, GitHub, an agent's raw
read) the same Markdown still reads as an ordinary list, table, or paragraph, so
a component never hides content.

Use a component when its shape says more than a sentence or a list would. Keep
prose primary: one figure per idea, at most two per section, with a sentence
between them that says what the figure shows.

The full vocabulary, every prop, and every data grammar are in
[references/components.md](references/components.md). Read it before using a
component you have not used before.

## Pick a component

Most notes need only these. The reference has the rest (39 in total).

| You want to show | Use |
| --- | --- |
| One or a few headline numbers | `bm-stat` |
| A comparison of a few values | `bm-bars` |
| A short ranked list | `bm-rank` |
| A small data table | `bm-table` |
| Progress toward a whole | `bm-meter` |
| A trend without axes | `bm-spark` |
| Steps that drop off | `bm-funnel` |
| A task list | `bm-check` |
| Work in lanes (now, next, later) | `bm-kanban` |
| Events in order | `bm-timeline` |
| A framed claim or summary | `bm-frame` |
| A short path (`a → b → c`) | `bm-flow` |
| A real graph, sequence, or state machine | a fenced ```mermaid block (not a component) |

If nothing fits, write prose or a plain Markdown table. Never invent a
component: an unknown name such as `::bm-pie` is not drawn and shows as raw text.

## Write a component

Every component opens with `::bm-name{attributes}` on its own line and closes
with `::` on its own line.

```markdown
The queue rewrite more than doubled steady-state throughput.

::bm-bars{title="Throughput" caption="Before and after the queue rewrite"}
- Before: 42/s
- **After: 96/s**
::
```

Rules that matter:

- **Data goes in the body as Markdown**, not in an attribute: `- label: value`
  rows, a table for grids. **Bold** marks the current or chosen row, *italic*
  recedes, ` — note` adds a side note.
- **Attribute values are double-quoted and cannot contain `"`.** There is no
  escape. Inside a `query`, quote phrases with single quotes:
  `query="search 'queue rewrite'"`.
- **Task state stays in Markdown.** Use `- [ ]` and `- [x]` inside `bm-check` and
  `bm-kanban`; people can tick boxes from the rendered note.
- **Never put HTML, scripts, styles, or classes in a note**, and never wrap a
  component in a code fence (a fenced block stays literal code).
- **Use only registered props.** An unknown attribute makes the directive
  unparseable, and it shows as raw text.

A checklist and a board:

```markdown
::bm-check{title="Launch"}
- [x] Freeze the schema
- [ ] Write the migration note — due Friday
::

::bm-kanban{title="Roadmap"}
### Now
- **Billing page**
- [ ] Usage emails

### Next
- *Partner portal*
::
```

## Live views over the knowledge base

A component with a `query` attribute shows current data from the project it
lives in. Prefer a live view to a hand-copied number when the note describes its
own knowledge base, because copied counts go stale.

```markdown
::bm-stat{label="Open decisions" query="notes | where type == decision and status == open | count"}
::

::bm-bars{title="Open decisions by owner" query="notes | where type == decision and status != done | group owner | sort count desc"}
::

::bm-table{title="Due soon" query="notes | where tags contains infra and due < 2026-10-15 | fields title, owner, due | sort due"}
::

::bm-timeline{title="Recent edits" query="activity 7d"}
::

::bm-rank{title="Related notes" query="search 'checksum conflict' | limit 5"}
::
```

A pipeline reads left to right: a source (`notes`, `search 'phrase'`,
`activity 7d|30d`), then optional stages in order: `where`, `group`, `count` or
`sum field`, `fields`, `sort`, `limit` (1–50). The reference lists every
operator and which components accept which shapes.

Shapes that work:

- **Charts, ranks, and bars need a number per row**: add `group field`, `count`,
  or `sum field`, or use a `search` source.
- **Sparks, KPIs, and plots need several rows** (`group`, `daily`, or `search`),
  not a single `count`.
- **`bm-table` takes `fields`**; `bm-timeline` takes `activity` without `daily`;
  `bm-activity` takes `activity 30d | daily`.

Semantics to state honestly in the surrounding prose:

- `notes` views are exact counts over indexed frontmatter. Search views are
  ranked and bounded; call them matches, never "all".
- `!=` keeps notes that lack the field. `group` puts notes without the field in
  a `none` group.
- Frontmatter numbers are stored as text; comparisons and sums treat decimal text
  as numbers and ignore everything else.

### Check the rendered result before you quote it

In Basic Memory Cloud, `read_note(view="text")` returns the note as a reader
sees it: every component as plain Markdown, with live queries run now. Read it
back after writing a live view and confirm the view has rows before you state a
number in prose. A query over a field no note has renders an empty view.

Elsewhere, only the browser runs the query, so run the equivalent read yourself
(for example `search_notes` with metadata filters; see the
memory-metadata-search skill).

A live chart's body is its **snapshot**: the rows readers see when live data
cannot load. A new live view can leave the body empty; people fill it with
**Freeze snapshot** in the editor. If you add snapshot rows yourself, label them
as a snapshot in prose and keep them in the chart's row grammar.

## Edit a note that already has components

- Change one component with `edit_note` (`find_replace` on its directive line or
  rows). Do not rewrite the whole note to change one value.
- The rich editor normalizes formatting when a person edits the note: attribute
  order changes and YAML blocks come back as JSON. Values survive; do not fight
  the formatting.
- Keep the closing `::` lines. An unclosed component swallows the rest of the
  note into its body.

## When a component shows an error

A broken component renders a coded problem and a fix inside its own frame; the
rest of the note still renders. In Basic Memory Cloud, `write_note` and
`edit_note` list these problems under **Component problems** after the save, so
fix them right away with `edit_note`. `comark(7)` in the manual
(`memory://man/comark(7)`) is a quick reference to the syntax and codes. The
codes you will meet most:

| Code | Meaning | Fix |
| --- | --- | --- |
| `query-measure` | The chart has no number per row | Add `group`, `count`, or `sum`, or use `search` |
| `query-series` | A trend from one value | Use `group`, `daily`, or `search` with `limit` ≥ 2 |
| `query-escaped-quote` | `\"` inside a query | Use single quotes inside the query |
| `query-field` / `query-operator` | Bad field name or operator | Fields are `[A-Za-z0-9_-]` with dotted paths; ops are `== != < <= > >= contains` |
| `query-stage-order` | Stages out of order | `where`, `group`, `count`/`sum`, `fields`, `sort`, `limit` |
| `chart-body-empty` | No rows in the body | Add `- label: value` rows |
| `chart-body-number` | A row without a number | Every row needs a numeric value |

An unknown component or attribute is not drawn at all and shows as raw text;
check names and props against the reference.

## Checklist

1. Does a figure beat a sentence here? If not, write the sentence.
2. Is the component name and every prop in the reference?
3. Is data in the body as Markdown rows or a table, with no `"` inside values?
4. For a live view: does the pipeline give the shape this component needs, and
   did you check the numbers with a read of your own?
5. Is there prose around the figure saying what it shows and whether a count is
   exact or a ranking?
