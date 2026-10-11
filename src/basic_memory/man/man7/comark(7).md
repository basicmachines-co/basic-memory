---
title: comark(7)
type: manpage
section: 7
name: comark
summary: write ::bm-* components that render as charts, tables, boards, and live views
generated: hand
---

# comark(7)

## NAME

**comark** — write `::bm-*` components that render as charts, tables, boards, and live views

## SYNOPSIS

```
::bm-name{attribute="value" ...}
Markdown body: rows, a table, a task list, or prose
::
```

Every component opens with `::bm-name{...}` on its own line and closes with `::`
on its own line. Attribute values are double-quoted and cannot contain `"`.

## DESCRIPTION

Comark components are block directives inside an ordinary Markdown note. Basic
Memory Cloud draws them in Notes, the Wiki, and shared pages. Everywhere else (a
local editor, GitHub, a raw `read_note`) the same Markdown reads as the list,
table, or paragraph it is, so a component never hides content.

Data goes in the body as Markdown, not in an attribute. A component with a
`query` attribute is a live view: the host runs the query over the project the
note lives in each time the note is viewed.

This page is the quick reference. The `memory-comark` skill's
`references/components.md` has every prop and every closed value set.

## VOCABULARY

Thirty-nine components are registered. Nothing else is a component, and an
unknown name such as `::bm-pie` is not drawn.

Content and layout: `bm-frame`, `bm-ascii`, `bm-image`, `bm-svg`, `bm-stat`,
`bm-kpi`, `bm-table`, `bm-timeline`, `bm-tree`, `bm-spec`, `bm-compare`,
`bm-diff`, `bm-sheet`, `bm-check`, `bm-invoice`, `bm-kanban`, `bm-card`,
`bm-card-group`, `bm-field`, `bm-field-group`, `bm-meter`, `bm-spark`, `bm-rank`

Charts: `bm-bars`, `bm-funnel`, `bm-stack`, `bm-waffle`, `bm-plot`, `bm-uptime`,
`bm-slope`, `bm-bullet`, `bm-waterfall`, `bm-gantt`, `bm-heatmap`, `bm-matrix`,
`bm-cells`, `bm-flow`, `bm-activity`, `bm-calendar`

A real graph, sequence, or state machine is a fenced ```mermaid block, which is
ordinary Markdown and not a component.

## BODY GRAMMAR

| Write | Means |
| --- | --- |
| `- label: value` | One row. The label ends at the first `: `. |
| `**bold**` row | The accent: the current, chosen, or total row. |
| `*italic*` row | Recedes: next, rejected, or an aside. |
| `value — note` | A side note after the row. `--` works too. |
| `42/s`, `1,200`, `72%` | A number with its unit; the number is plotted. |
| `a → b → c` | A path. `->` works too. |
| `ok×40` | A run of forty. |
| A Markdown table | Grid data: the header labels columns, the first column labels rows. |
| `- [ ]`, `- [x]` | Task state in `bm-check` and `bm-kanban`. |

## LIVE QUERIES

A `query` attribute is a pipeline read left to right: one source, then optional
stages in this order.

| Part | Grammar |
| --- | --- |
| Source | `notes` (exact), `search 'phrase'` or `search 'phrase' hybrid` (ranked), `activity 7d` or `activity 30d` |
| `where` | `field op value` joined by `and`; ops `==` `!=` `<` `<=` `>` `>=` `contains` |
| `group field` | One row per value, with its count (or sum) |
| `count`, `sum field` | One number, or one per group after `group` |
| `fields a, b` | Note rows with these columns |
| `daily` | Activity only: one count per day |
| `sort field [desc]` | Sort by a field, `count`, or `sum` |
| `limit n` | 1 to 50 rows |

Quote phrases with single quotes: `query="search 'checksum conflict'"`.

Live components: `bm-table`, `bm-stat`, `bm-rank`, `bm-spark`, `bm-kpi`,
`bm-timeline`, `bm-bars`, `bm-funnel`, `bm-stack`, `bm-plot`, `bm-activity`.
Charts and ranks need a number per row (`group`, `count`, `sum`, or `search`).
Sparks, KPIs, and plots need several rows. `notes` counts are exact; `search`
results are ranked and bounded, so describe them as matches.

## PROBLEM CODES

A broken component renders its code, the problem, and a fix inside its own
frame; the rest of the note still renders. Most codes name their fix in the
message.

| Code | Meaning |
| --- | --- |
| `unknown-element` | The name is not a registered component |
| `invalid-attribute` | An attribute the component does not accept |
| `invalid-attribute-value` | A value outside the attribute's closed set or range |
| `missing-attribute` | A required attribute is absent |
| `invalid-chart-body` | The body does not fit the chart; see the `chart-body-*` code |
| `chart-body-empty` | No rows in the body |
| `chart-body-number` | A row without a number |
| `query-measure` | A chart query with no number per row |
| `query-series` | A trend from a single value |
| `query-stage-order` | Stages out of order |
| `query-escaped-quote` | `\"` inside a query; use single quotes |
| `query-field`, `query-operator` | A bad field name or operator |
| `authored-html` | HTML in the note; it is never rendered |

Other `chart-body-*` and `query-*` codes follow the same pattern: the suffix
names the part of the body or pipeline that is wrong.

## CHECKING YOUR WORK

In Basic Memory Cloud:

- `write_note` and `edit_note` list the components that will render as errors,
  under **Component problems** (a `component_problems` field in JSON output).
  The note is saved either way; fix the problems with `edit_note`.
- `read_note(view="text")` returns the note as a reader sees it: each component
  as plain Markdown, with live queries run now. Use it to confirm a live view
  has rows before you quote its numbers. Edit from the default view, which is
  the stored source.

## EXAMPLES

```
The queue rewrite more than doubled steady-state throughput.

::bm-bars{title="Throughput" caption="Before and after the queue rewrite"}
- Before: 42/s
- **After: 96/s**
::

::bm-bars{title="Open decisions by owner" query="notes | where type == decision and status != done | group owner"}
::
```

## GOTCHAS

- [gotcha] An unclosed component swallows the rest of the note into its body.
- [gotcha] A component inside a code fence is literal code and is not drawn.
- [gotcha] The rich editor normalizes attribute order and formatting; values survive.
- [gotcha] A query over a field no note has renders an empty view, not an error.

## SEE ALSO

- see_also [[write-note(3)]]
- see_also [[edit-note(3)]]
- see_also [[read-note(3)]]
