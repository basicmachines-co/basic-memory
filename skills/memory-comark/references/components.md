# Comark component reference

The complete authored vocabulary for `::bm-*` components in Basic Memory notes:
every component, its props and closed value sets, the data grammars, and live
query pipelines. Basic Memory Cloud renders these in Notes, the Wiki, and shared
pages; elsewhere the Markdown stays readable as written.

## The registered vocabulary

Thirty-nine components are registered. Nothing outside this list is a component.

**Content and layout (23)** — `bm-frame`, `bm-ascii`, `bm-image`, `bm-svg`, `bm-stat`, `bm-kpi`, `bm-table`, `bm-timeline`, `bm-tree`, `bm-spec`, `bm-compare`, `bm-diff`, `bm-sheet`, `bm-check`, `bm-invoice`, `bm-kanban`, `bm-card`, `bm-card-group`, `bm-field`, `bm-field-group`, `bm-meter`, `bm-spark`, `bm-rank`

**Charts (16)** — `bm-bars`, `bm-funnel`, `bm-stack`, `bm-waffle`, `bm-plot`, `bm-uptime`, `bm-slope`, `bm-bullet`, `bm-waterfall`, `bm-gantt`, `bm-heatmap`, `bm-matrix`, `bm-cells`, `bm-flow`, `bm-activity`, `bm-calendar`

Of these, thirty-one form the deterministic Markdown Graphs vocabulary that strict export can project as inert ASCII. The four media components (`bm-ascii`, `bm-image`, `bm-svg`) plus `bm-kanban` are Basic Memory additions; export treats the media three as media rather than as chart data.

## Choose a component

| The content is | Use | Avoid |
| --- | --- | --- |
| A framed claim or section | `bm-frame` | A generic card made with HTML |
| One headline metric | `bm-kpi` | A table with one value |
| Two to four plain metrics | `bm-stat` | KPI when no trend exists |
| A compact trend without axes | `bm-spark` | Plot |
| A series that needs scale | `bm-plot` | Spark |
| One fill from zero to one | `bm-meter` | Bullet |
| Actual versus target | `bm-bullet` | Meter |
| Before and after values | `bm-slope` | Bars |
| Two small histograms | `bm-bars` | Rank |
| A short ranked list | `bm-rank` | Bars |
| Parts of a whole | `bm-stack` | Pie chart |
| About one hundred filled cells | `bm-waffle` | Stack when cells add no value |
| A small filled or empty grid | `bm-cells` | Waffle |
| Drop-off steps | `bm-funnel` | Flow |
| A short process or relationship path | `bm-flow` | Mermaid for two or three hops |
| A real graph, state machine, or sequence | A fenced ```mermaid block | `bm-flow` forced into a shape it cannot hold |
| Events in order | `bm-timeline` | A paragraph of timestamps |
| Nested files or concepts | `bm-tree` | A flat list |
| Overlapping work | `bm-gantt` | Timeline |
| Daily status | `bm-uptime` | Activity |
| Daily counts over weeks or months | `bm-activity` | Calendar |
| A month with marked days | `bm-calendar` | Activity |
| A labeled intensity grid | `bm-heatmap` | Matrix |
| Exact values on two axes | `bm-matrix` | Heatmap |
| A running total | `bm-waterfall` | Stack |
| A small data table | `bm-table` | A hand-built HTML table |
| A grouped document table | `bm-sheet` | Table |
| A label/value contract | `bm-spec` | Stat |
| A feature comparison | `bm-compare` | Matrix |
| A compact change summary or unified patch | `bm-diff` | A prose bullet list |
| A task list | `bm-check` | Task state in a prop string |
| Lanes of work or tasks | `bm-kanban` | Three separate checklists |
| Invoice parties and line items | `bm-invoice` | Table |
| A small group of linked resources or summaries | `bm-card-group` with `bm-card` | Raw HTML cards |
| A typed property contract | `bm-field-group` with `bm-field` | A form or opaque prop string |
| A local image as a character field | `bm-ascii` | Pasted ASCII art |
| A local image as a halftone or dither | `bm-image` | A CSS filter on a plain image |
| A local SVG, optionally with bounded motion | `bm-svg` | Inline `<svg>` markup |
| A live view over this knowledge base | A query-backed component (see below) | Hand-copied counts that go stale |

## Use the directive form

Every component is a block directive and must close:

```markdown
::bm-meter{title="Migration" value="0.67" ticks="14" caption="Users table" tone="positive"}
::
```

Do not put the directive inside a code fence when it should render. Do not treat a code fence as a component when parsing or exporting.

### Two attribute forms

Inline braces are the compact form. A leading YAML property block, fenced by `---` lines, is the equivalent long form and is required for structured array data.

```markdown
::bm-bars
---
title: Throughput
items:
  - label: Before
    value: 42
    display: 42/s
  - label: After
    value: 96
    display: 96/s
caption: Before and after the queue rewrite
---
::
```

Both forms produce the same canonical node, and the serializer picks between them by attribute count: up to six attributes stay inline, and a denser set becomes a `---` block. Typed array attributes always become a block regardless of the count.

Never author a ```yaml attribute fence. Comark's own defaults would emit one, so the profile sets `blockAttributesStyle: 'frontmatter'` and raises `maxInlineAttributes` (see `BASIC_MEMORY_COMARK_RENDER_OPTIONS` in `comark-profile.ts`). A fence carrying attributes would contradict fences staying literal code and would leave one note with two spellings for the same thing.

Editing a note in the rich editor rewrites its canonical Markdown: attribute order is normalized, and a structured YAML block comes back as JSON inside the `---` delimiters. Values survive; your formatting does not. Do not hand-tune directive formatting you expect to persist through an editor session.

### Closed value sets

| Prop | Components | Values |
| --- | --- | --- |
| `variant` | `bm-frame` | `default`, `subtle`, `emphasized` |
| `density` | frame, table, timeline, tree, diff, sheet, check, invoice, card, card-group, field, field-group | `comfortable`, `compact` |
| `visual` | `bm-frame` | `dither`, `dither-mesh`, `dither-scan` (omit for an ordinary frame) |
| `tone` | `bm-stat`, `bm-meter` | `neutral`, `positive`, `warning`, `critical` |
| `glyphs` | `bm-spark`, `bm-kpi`, `bm-activity` | `bar`, `shade`, `ascii`, `hash` |
| `palette` | spark, kpi, diff, check, activity, calendar, image | `mono`, `duo`, `multi` (`bm-image` accepts `mono`, `duo`) |
| `kind` | `bm-diff` | omit for the summary form, `unified` for a patch |

Frame visuals are static, decorative, and theme-derived. Do not invent shader code, colors, animation, or interactive behavior in a note.

## Use ordinary Markdown children

Use Markdown children for layouts whose content is naturally authored as Markdown.

### Table

```markdown
::bm-table{title="Release readiness" caption="Current project state" density="compact"}
| Area | State | Owner |
| --- | --- | --- |
| Wiki | Ready | Product |
| Graph | Ready | Platform |
::
```

### Checklist

```markdown
::bm-check{title="Conformance" density="compact" palette="duo"}
- [x] Store the bundle as UTF-8 Markdown files
- [x] Preserve unknown frontmatter fields
- [ ] Add citations for external claims
::
```

### Kanban

`bm-kanban` follows mdxcn's GraphBoard. Each heading is a lane and the list under it holds the lane's items. The first heading level in the board names the lanes (`##` or `###`); deeper headings stay inside their lane. Each lane shows its item count, and screen readers hear one summary sentence ("Shipped: 2, Now: 2. 4 items.").

- **Bold** marks what is happening now and draws in the accent color.
- *Italic* marks what comes next and recedes.
- ` — note` (or ` -- note`) puts a short note under the item.
- Task items (`- [ ]`, `- [x]`) keep their checkbox, and done tasks recede.

Lanes sit side by side and stack on narrow screens. Task checkboxes toggle from the Notes preview and the MCP reader, like any Markdown task; move an item between lanes by editing the source, since drag-and-drop is not implemented. Strict export writes the board as ordinary headings and lists.

```markdown
::bm-kanban{title="Roadmap"}
### Shipped
- Callout, Steps, Terminal

### Now
- **Children for every graph**
- Board and Score — this drop

### Later
- *Vue port* — if someone asks twice
::
```

### Tables: table, sheet, spec, compare, and invoice

Table-shaped components share one style, after mdxcn: a muted header over a dotted rule, no column rules, and each column aligned the way the Markdown table says (`---:` right-aligns numbers). A **bold** last row reads as a total, with a rule above it and the accent color.

- **`bm-spec`** takes `- Label: value` rows, and still accepts a two-column table. Labels recede and values line up beside them.
- **`bm-compare`** draws a cell that says only `yes` or `no` as ✓ or –. `accent="Column"` colors the column whose header matches. The first column holds the feature names and does not wrap.
- **`bm-invoice`** puts each heading before the line items (`### From`, `### Bill to`) in its own column, side by side, then the line-items table at full width. A bold last row is the amount due.

```markdown
::bm-spec{title="Type"}
- Family: Geist Mono
- Size: 14 / 21
::

::bm-compare{title="Plans" accent="Studio"}
| | Solo | Studio |
| --- | --- | --- |
| Registry | yes | yes |
| Private source | no | yes |
::
```

### Timeline, tree, rank, sheet, spec, compare, and invoice

Author their rows as ordinary Markdown. Do not invent an opaque item string when Markdown already represents the structure.

```markdown
::bm-timeline{title="Launch"}
1. 2026-09-01 — RFC accepted
2. **2026-09-20 — Beta**
3. *2026-10-15 — General availability*
::

::bm-tree{title="Project"}
- notes
  - decisions
  - tasks
- wiki
::

::bm-rank{title="Most linked"}
1. Pricing model — 14
2. Usage emails — 9
3. Partner portal — 4
::
```

In a timeline, **bold** marks the current event and *italic* an upcoming one.

### Cards and fields

Cards and fields use ordinary Markdown children for their descriptions, links, lists, and examples. The component attributes only describe the ASCII frame and typed label.

```markdown
::bm-card-group{title="Resources" density="compact"}
::bm-card{title="Component guide" density="compact"}
Use **canonical Markdown** for the card body.
::
::

::bm-field-group{title="Frontmatter" density="compact"}
::bm-field{name="title" type="string" required="true" density="compact"}
Human-readable note title.
::
::
```

Do not put card copy, field descriptions, links, arbitrary classes, or styles in attributes.

## Media components

The three media components take a single ordinary Markdown image child pointing at a project-local file. They never accept a remote URL, inline markup, or a data URI, and their alt text stays the accessible label.

```markdown
::bm-ascii{title="Geometric wave" preset="paper" detail="high" contrast="edge" polarity="dark" motion="wave"}
![Layered dark blue waves curling around a pale circular moon](/ascii-wave.svg)
::

::bm-image{title="Halftone wave" effect="halftone" detail="high" palette="duo" motion="pulse"}
![Layered dark blue waves curling around a pale circular moon](/ascii-wave.svg)
::

::bm-svg{title="Static baseline" fit="contain" motion="none"}
![Layered dark blue waves curling around a pale circular moon](/ascii-wave.svg)
::
```

| Prop | Component | Values |
| --- | --- | --- |
| `preset` | `bm-ascii` | `crisp` (default), `paper` |
| `detail` | `bm-ascii`, `bm-image` | `low`, `medium` (default), `high` |
| `contrast` | `bm-ascii` | `soft`, `edge` (default) |
| `polarity` | `bm-ascii` | `dark` (default), `light` |
| `motion` | `bm-ascii` | `none` (default), `drift`, `pulse`, `wave`, `rain`, `glitch` |
| `effect` | `bm-image` | `dither`, `halftone` |
| `palette` | `bm-image` | `mono`, `duo` |
| `motion` | `bm-image` | `none`, `drift`, `pulse` |
| `fit` | `bm-svg` | `contain` (default), `cover` |
| `motion` | `bm-svg` | `none`, `drift`, `pulse`, `float`, `reveal` |

`bm-ascii` with `motion="rain"` or `motion="glitch"` generates a full-frame procedural field and needs no image child. All motion becomes static when the viewer requests reduced motion. An SVG that animates itself should use `motion="none"` so the wrapper adds nothing.

## Mermaid diagrams

A fenced ```mermaid block renders as a diagram in Notes, Wiki, and the MCP app reader. It is ordinary Markdown, not a component, and needs no directive and no library import.

Reach for `bm-flow` first for a two- or three-hop path, because it exports deterministically and matches the surrounding ASCII language. Use Mermaid when the content is a real graph, state machine, sequence, or class relationship that a flow row cannot carry. Never invent Mermaid syntax to fake a chart the vocabulary already covers, and never claim a relationship the source does not support.

## Query-backed live views

This is implemented, not proposed. A component with an `operation` attribute becomes a read-only view over the knowledge base that contains it. Prose, query, and presentation stay canonical Markdown; the host supplies current data when the note is viewed.

The note selects the presentation, `operation` names a stable read capability, and named attributes supply its inputs. The note cannot supply a URL, credentials, workspace, or project — the host owns identity.

### Write live views as pipelines

Prefer this form. A `query` attribute pipes rows into the component, read left to right
like a Knap filter chain: a source, then optional stages in this order.

```markdown
::bm-bars{title="Open decisions by owner" query="notes | where type == decision and status != done | group owner | sort count desc"}
::

::bm-rank{title="Points by owner" query="notes | group owner | sum points | sort sum desc | limit 5"}
::

::bm-stat{label="Active objectives" query="notes | where type == objective and status == open | count"}
::

::bm-table{title="Infra work due soon" query="notes | where tags contains infra and due < 2026-10-15 | fields title, owner, due | sort due"}
::

::bm-activity{title="Edits per day" query="activity 30d | daily"}
::
```

| Part | Grammar |
| --- | --- |
| Sources | `notes` (exact indexed metadata), `search 'phrase'` or `search 'phrase' hybrid` (ranked), `activity 7d` or `activity 30d` |
| `where` | `field op value` joined by `and`; ops `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`. Values are words, numbers, `'quoted phrases'`, or `null` |
| `group field` | One row per value, with its count (or sum) |
| `count`, `sum field` | One number over the filtered set, or per group after `group` |
| `fields a, b, c` | Note rows with these columns (not after an aggregate) |
| `daily` | Activity only: one exact count per day in the window |
| `sort field [desc]` | Sort a grouped result by its field, `count`, or `sum` |
| `limit n` | 1 to 50 rows |

Quote phrases with single quotes: the `query` attribute is itself double-quoted, and a
Comark attribute has no escape for `"`, so `query="search 'checksum conflict'"` is the form
that works. Double quotes are accepted only where the attribute is single-quoted. Writing
`\"phrase\"` inside the attribute fails with `query-escaped-quote`. A phrase may contain
`|`, the other quote, or a backslash-escaped quote of its own kind (`'don\'t'`).

Search returns ranked rows (`title`, `permalink`, `score`), bounded rather than exhaustive.
Add `hybrid` for meaning-based ranking; `where` after a search takes `==` filters on distinct
fields, and `limit` defaults to 20.

Live views refresh when the project's notes change, at most once every 30 seconds per view,
so a note being edited does not spend the query budget on every autosave.

Activity views count content changes (`note.*` and `folder.*` events), not indexing or
other bookkeeping rows.

Semantics worth stating in prose: `!=` keeps every note whose value is not the given one,
including notes that lack the field or hold text; ordered comparisons (`<`, `<=`, `>`, `>=`),
sums, and numeric sorts treat plain decimal text as numbers (core stores frontmatter numbers
as text) and ignore other values; `contains` matches an array member or a substring;
`search` filters compare with `==` only, and `search` stages accept only `where` and `limit`.

Live components: `bm-table`, `bm-stat`, `bm-rank`, `bm-spark`, `bm-kpi`, `bm-timeline` (activity
without `daily`), `bm-bars`, `bm-funnel`, `bm-stack`, `bm-plot`, and `bm-activity` (with
`daily`). Charts and ranks need a number per row, so add `group`, `count`, or `sum`, or use
`search`. Sparks, KPIs, and plots draw a trend, so they need several rows: `group`, `daily`, or
`search`, and a `limit` of at least 2 (`count` or `sum` alone gives one point).

A live chart's Markdown body is its last frozen snapshot. Readers see live rows; when the
query cannot run, the snapshot shows with the reason. In the editor the body sits behind a
**snapshot rows** toggle (marked *empty* until filled), and **Freeze snapshot** rewrites it
from the current rows. Strict export uses the snapshot and refuses a live view that has
none.

In the component's property form the query is a multi-line field. An invalid pipeline
shows its coded problem and fix there and is not saved, so the chart keeps its last
working query while you type.

Presentation of live results:

- Activity timelines read as `Oct 9, 9:41 PM — Title — created`, in the reader's time zone.
- A table whose titles link to notes leaves out the internal `permalink` column.
- A `sum` result leaves out a `none` group that sums to zero (notes without the grouped
  field and without the summed field); a counted `none` group stays.

An invalid pipeline renders its coded problem and fix inside the component in preview,
and canonical validation rejects it.

### The v1 catalog (legacy operations)

Existing notes use these operations and they keep working. Write new live views as pipelines.


| Operation | Inputs | Components |
| --- | --- | --- |
| `bm.search.metadata` | `filter`, `aggregate`, `group-by`, `fields`, `sort`, `limit` | `bm-stat`, `bm-table`, `bm-rank` |
| `bm.search.fts` | `filter`, `query`, `limit` | `bm-table`, `bm-rank` |
| `bm.search.hybrid` | `filter`, `query`, `limit` | `bm-table`, `bm-rank` |
| `bm.activity.recent` | `window`, `metric`, `interval`, `limit` | `bm-table`, `bm-timeline`, `bm-kpi`, `bm-spark` |

Any other operation name, or any other component/operation pairing, is rejected as unsupported.

```markdown
::bm-table{operation="bm.search.metadata" filter="type=decision;status=open" fields="title,priority,owner,due" sort="due" limit="20" title="Open decisions"}
::

::bm-stat{operation="bm.search.metadata" filter="type=objective;status=active" aggregate="count" label="Active objectives"}
::

::bm-rank{operation="bm.search.metadata" filter="type=decision" aggregate="count" group-by="owner" title="Open decisions by owner"}
::

::bm-table{operation="bm.search.fts" query="checksum conflict" limit="10" title="Exact mentions"}
::

::bm-rank{operation="bm.search.hybrid" query="why are edits getting lost?" limit="8" title="Related notes"}
::

::bm-spark{operation="bm.activity.recent" metric="notes-updated" window="30d" interval="day" title="Updates over time"}
::
```

### Validation rules

These are enforced at the boundary, so an authored note that breaks one renders an error inside that component rather than silently returning something else.

- Besides the operation's own inputs, only `operation`, `version`, `title`, `label`, `caption`, and `density` are accepted. Any other attribute is rejected.
- `limit` is an integer from 1 to 50.
- `filter` is at most ten unique `field=value` predicates joined with `;`. Both halves must be non-empty. `null` as a value matches absent or null metadata. Nested scalars use dotted paths. The whole string is capped at 1000 characters; other attributes at 500.
- `fields` is at most ten comma-separated frontmatter fields matching `[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*`. `sort` is one ascending field with the same shape.
- `bm.search.metadata`: stats and ranks require `aggregate="count"`; `group-by` is required for `bm-rank` and rejected elsewhere; `fields` and `sort` require a table; a table rejects `aggregate`.
- `bm.activity.recent`: `window` is `7d` or `30d`, `metric` is `notes-updated`, `interval` is `day`. KPI and spark require `metric="notes-updated"`.
- `bm.search.fts` and `bm.search.hybrid` require a non-empty `query`.

### Say what the numbers mean

Metadata views make exact claims about the filtered set, and their counts are calculated before the row limit applies. FTS and hybrid views are lexical or ranked results over a bounded window — describe them as matches or rankings, never as exhaustive coverage. Similarity is ranking context, not a percentage of all relevant knowledge.

### Versioning

The optional `version` attribute versions the selected operation's inputs and result shape; it does not version the global Comark profile. Only `version="1"` is accepted today, and omitting it means contract version 1 permanently rather than whichever version is newest.

### Failure is component-local

Treat every live component as an isolated failure boundary. A query error, unsupported operation, timeout, or invalid returned shape renders a concise description inside that component while the rest of the note continues rendering. Never replace the whole note with the failure, and never expose stack traces, credentials, or internal query details in an authored surface.

Results are inert typed data and are never reparsed as Markdown. No query result is accepted directly from an SSE payload; SSE only prompts a re-fetch.

Strict Wiki export rejects parameterized operations until it can capture parameter-bound snapshots. It never substitutes an operation-only snapshot taken with different filters. Canonical Markdown stays portable either way.

## Write chart data as a Markdown body

Prefer this form for every chart. The body is ordinary Markdown, so the note still reads
as a list or table on GitHub, in Obsidian, or in an agent's raw read, and Basic Memory
draws the figure. The grammar matches mdxcn's, so the same conventions work in both.

| Write | Means |
| --- | --- |
| `- label: value` | One row. The label ends at the first `: `. |
| `**bold**` row or label | The accent: the current, chosen, or total row. |
| `*italic*` row or label | Recedes: next, rejected, or an aside. |
| `value — note` | A side note after the row. `--` works too. |
| `42/s`, `1,200`, `72%` | A number with its unit. The text is shown; the number is plotted. |
| `a → b → c` | A path. `->` works too. |
| `ok×40` or `ok*40` | A run of forty. Prefer `×`, because Markdown can read `*` pairs as italics. |
| A table | Grid data. The header labels the columns and the first column labels the rows. |

A chart reads its body only when its data attribute (`data`, `value`, `rows`, `days`, or
`marks`) is absent. With the attribute present, the body stays prose under the figure.
Readers see the figure. In the editor the rows sit behind a **source rows** toggle under
the figure (and in the component's property form), so a change redraws the chart; rows
the chart cannot read open automatically so the problem can be fixed in place. A body that does not fit renders a coded message with a fix inside
that component, and profile validation rejects it as `invalid-chart-body`.

```markdown
::bm-bars{title="Throughput" caption="Before and after the queue rewrite"}
- Before: 42/s — cold cache
- **After: 96/s**
::

::bm-funnel{title="Install"}
- docs: 12,400
- **copy: 4,100**
- ship: 860
::

::bm-bullet{title="Budgets"}
- API: 72% / 80
- Search: 40 / 50 / 100
::

::bm-slope{title="Coverage"}
- auth: 41 → 88
::

::bm-waterfall{title="Margin"}
- Start: 100
- Refunds: -6
- **Net: 94**
::

::bm-gantt{title="Launch"}
- RFC: 0% 40% 100%
- **Build: 25% 80% 60%**
::

::bm-uptime{title="API" columns="30"}
ok×40 degraded ok×20 down×2
::

::bm-activity{title="Commits"}
- 2026-08-03: 0 1 4 2 0×2 3
::

::bm-calendar{title="August 2026" year="2026" month="8"}
- 12: launch
- **27: today**
::

::bm-waffle{title="Tests"}
73% — tests green
::

::bm-matrix{title="Latency"}
| | p50 | p95 |
| --- | --- | --- |
| Read | 12 | 42 |
| **Write** | 18 | 57 |
::

::bm-flow{title="Request path"}
- request → **middleware** → handler
::
```

| Chart | Body |
| --- | --- |
| `bm-bars`, `bm-funnel`, `bm-stack` | `- label: value` rows; bold marks the funnel stage |
| `bm-bullet` | `- label: value / target`, or `/ max` as a third number; rows without a max share one scale |
| `bm-slope` | `- label: before → after` |
| `bm-waterfall` | `- label: delta`; the first row opens, a bold row or the last row is a total |
| `bm-gantt` | `- label: start end complete` as fractions or percents |
| `bm-plot` | `- label: value` rows, or a paragraph of numbers |
| `bm-uptime` | A paragraph of `ok`, `degraded`, `down` with runs |
| `bm-activity` | `- YYYY-MM-DD: counts…` for consecutive days, with runs |
| `bm-calendar` | `- day: label`; the bold day is today. `year` and `month` stay attributes |
| `bm-waffle` | A paragraph: `73% — caption` |
| `bm-heatmap`, `bm-matrix`, `bm-cells` | A Markdown table; `bm-cells` accepts `x` for filled |
| `bm-flow` | One path per list item or paragraph |

### Plain-text figures

Every chart has a framed plain-text twin, which strict export writes in place of the
figure. Do not hand-draw this ASCII in a note; author the component and let the renderer
draw it.

```text
+------------------ [ INSTALL ] -------------------+
|                                                  |
|   docs  ████████████████████████  12,400  100%   |
| ▸ copy  ████████················   4,100   33%   |
|   ship  ██······················     860    7%   |
|                                                  |
+--------------------------------------------------+
```

## Use the bounded data grammar (legacy attributes)

Existing notes use this attribute grammar and it keeps working. Write new charts with a
Markdown body instead.


Chart attributes use a compact string grammar:

- `;` separates records.
- `:` separates a label, numeric fields, and an optional display value.
- `,` separates series values, labels, matrix columns, or matrix cells.
- `>` separates nodes in a flow row.
- `|` separates the fixed fields of a compact diff row.

Prefer the YAML property block when a label contains `:`, `;`, or `,`, because the structured form preserves punctuation that the delimited grammar would split.

### Common series

```markdown
::bm-bars{title="Throughput" data="Before:42:42/s;After:96:96/s"}
::

::bm-stack{title="Bundle" data="js:54:54kb;css:28:28kb;assets:18:18kb"}
::

::bm-funnel{title="Install" data="docs:12400:12,400;copy:4100:4,100;ship:860:860" stage="copy"}
::
```

### Numeric series

```markdown
::bm-spark{title="Latency" data="42,55,68,51,76,81,104,92,110" caption="Last nine deploys"}
::

::bm-plot{title="P95" data="42,55,68,51,76,81,104" labels="Mon,Sun" rows="6"}
::

::bm-kpi{title="Rows migrated" value="1.2M" label="of 1.8M rows" hint="67%" data="120,180,320,440,590,810,1200"}
::
```

### Specialized rows

```markdown
::bm-slope{title="Coverage" data="auth:41:88;billing:72:74;docs:11:40"}
::

::bm-bullet{title="Budget" data="API:72:80:100:72%;Web:91:90:100:91%"}
::

::bm-waterfall{title="Margin" data="Start:100:start;Revenue:28:in;Costs:-17:out;End:111:end"}
::

::bm-gantt{title="Launch" data="RFC:0:0.4:1;Build:0.25:0.8:0.6;Review:0.7:1:0" columns="20"}
::
```

### Grids and paths

```markdown
::bm-heatmap{title="Deploys" columns="M,T,W,T,F" rows="API:1,2,4,3,2;Web:0,1,3,4,2"}
::

::bm-matrix{title="Latency" columns="p50,p95,p99" rows="Read:12,42,88;Write:18,57,110"}
::

::bm-cells{title="Coverage map" columns="A,B,C,D" rows="Core:1,1,0,1;Cloud:1,0,1,1"}
::

::bm-flow{title="Publish path" rows="note>index>wiki;note>graph>relations"}
::
```

### Status and calendar data

```markdown
::bm-uptime{title="API" data="ok,ok,degraded,ok,down,ok" columns="6"}
::

::bm-activity{title="Commits" days="2026-08-10:0;2026-08-11:1;2026-08-12:4"}
::

::bm-calendar{title="August 2026" year="2026" month="8" marks="12,18,27" today="27" weekStartsOn="1"}
::
```

### Diff

Use the default summary kind for small, non-file changes. Each row is `add`, `remove`, or `keep` followed by a label and value. The optional footer is `label|value`.

```markdown
::bm-diff{title="Bundle" rows="keep|vendor|84 kb;add|app|31 kb;remove|sourcemaps|12 kb" footer="shipped|103 kb" palette="duo"}
::
```

Use `kind="unified"` only for a canonical unified patch, placed in a fenced `diff` block.

```markdown
::bm-diff{title="Files" kind="unified" footer="shipped|103 kb" palette="duo"}
```diff
--- a/packages/note-renderer
+++ b/packages/note-renderer
@@ -1,2 +1,2 @@
-legacy styles
+graph harness
 canonical Markdown
```
::
```

## Use the structured data grammar

Three components accept typed arrays through the YAML property block. The parser validates them and carries them as lossless JSON through the existing string-valued chart attributes, so punctuation in labels survives.

`bm-bars` takes `items` of `{label, value, display?}`:

```markdown
::bm-bars
---
title: Throughput
items:
  - { label: "p50, warm", value: 42, display: 42/s }
  - { label: "p95, cold", value: 96, display: 96/s }
---
::
```

`bm-bullet` takes `items` of `{label, value, target, max, display?}`, where `max` is greater than zero:

```markdown
::bm-bullet
---
title: Budgets
items:
  - { label: API, value: 72, target: 80, max: 100, display: 72% }
  - { label: Web, value: 91, target: 90, max: 100, display: 91% }
---
::
```

`bm-matrix` takes `columns` as a string array and `rows` as `{label, values}`:

```markdown
::bm-matrix
---
title: Latency
columns: [p50, p95, p99]
rows:
  - { label: Read, values: [12, 42, 88] }
  - { label: Write, values: [18, 57, 110] }
caption: Milliseconds by operation
---
::
```

Constraints, all enforced without a fallback path:

- At most 128 rows, and at most 128 values in a matrix row.
- Every `label` and `display` is a non-empty string of at most 4096 characters, with no `<` and no control characters.
- Every numeric field is a finite number within `Number.MAX_SAFE_INTEGER`.
- A component carries `items` or `data`, never both. Both at once is ambiguous and is rejected by profile validation.
- Only `label`, `value`, `display` (bars), `label`, `value`, `target`, `max`, `display` (bullet), and `label`, `values` (matrix) are accepted keys. An extra key invalidates the row.

An invalid structured value is rejected rather than silently repaired, so validate the shape while authoring instead of relying on recovery.

## Keep deterministic values

- Use finite numeric values. Invalid numeric fields in the delimited grammar normalize to safe values; do not rely on that recovery path when authoring.
- Keep Gantt `start`, `end`, and `complete` values between `0` and `1`.
- Use `ok`, `degraded`, or `down` for uptime cells.
- Use ISO `YYYY-MM-DD` dates for activity rows.
- Use month numbers `1` through `12` and day numbers `1` through `31` for calendar data. `weekStartsOn` is `0` for Sunday or `1` for Monday.
- Do not author Timer or Countdown. Their wall-clock dependency is incompatible with deterministic Notes, Wiki, and strict export without a frozen `asOf` value.

The presentation is inspired by Markdown Graphs, but Basic Memory notes use Comark directives and the shared Vue renderer. Never paste React JSX or an invented ASCII twin into canonical note content.
