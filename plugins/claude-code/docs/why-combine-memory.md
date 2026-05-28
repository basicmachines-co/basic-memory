# Why combine Basic Memory with Claude's built-in memory

Claude Code already has memory — auto-memory, the short notes Claude keeps to itself
per project. So why add Basic Memory on top?

Because they're built for different jobs, and **the best setup uses both**. That's not
our marketing line — it's the [documented stance](https://docs.basicmemory.com/concepts/vs-built-in-memory)
of Basic Memory itself:

> "Basic Memory doesn't replace them — it works alongside them. The best setup uses both."

This plugin is the piece that makes "use both" happen automatically, instead of making
you copy context back and forth by hand.

## Two memories, two jobs

|  | Claude's working memory | Basic Memory |
|--|------------------------|--------------|
| **Like** | RAM — what just happened | A journal — what matters over time |
| **Holds** | build commands, debugging tips, recent patterns | decisions, research, meeting notes, project context |
| **Scope** | one project, local, invisible | cross-project, portable, syncable, **sharable** |
| **Search** | whatever's in the window | semantic + structured (by `type`, `status`, …) |
| **Lifecycle** | summarized, compacted, eventually lost | durable Markdown files you own |

Working memory is fast and automatic but shallow and disposable. Basic Memory is
deliberate, structured, and permanent. Neither is "better" — they compound.

## 1 + 1 = 3

The multiplier is what each makes possible *for the other*:

- Working memory knows you were "in the auth refactor." Basic Memory turns that into a
  **briefing**: the open decisions, the active tasks, the note where you rejected the
  single-token approach last week — with permalinks.
- When the context window compacts, working memory compresses and loses detail. The
  plugin has already written a **durable checkpoint** to Basic Memory, so the next
  session resumes with the thread intact.
- Working memory can't be searched, linked, or shared. Basic Memory can — so a decision
  you capture today is findable in three weeks, from another repo, or by a teammate.

Each session starts richer than the last. The graph is the part that accumulates.

## Who it's for

### The thinker — non-developer power user
Writers, researchers, consultants, planners who live in Claude (Code or Desktop), keep
a body of work that compounds over months, and don't touch git.

> *"What did we conclude about the pricing model?"* — Claude searches the graph and
> answers with the actual note, not a guess. A month of conversations stays one
> `search_notes` away.

**Wins:** picking up where you left off; never losing the texture of a long session;
recalling what you actually decided.

### The builder — developer
Uses git and GitHub, runs Claude Code as the primary pairing partner, works across
multiple repos.

> A multi-hour debugging session hits compaction. Instead of Claude "forgetting" what
> you already ruled out, the checkpoint records the dead-ends — so it doesn't suggest
> the thing that already failed.

**Wins:** decisions tied to the work; surviving long sessions without amnesia; code
archaeology weeks later (*why did we do it this way?*).

### The operator — PM, team lead, ops
Runs several projects through Claude, lives in decisions and status more than code,
often on a **team**.

> First session on a teammate's project: the SessionStart brief already shows the
> team's recent open decisions. You're oriented before you ask a single question.

**Wins:** no context bleeding between projects; team memory that compounds across
people; sharing a decision to the team in one gesture (`/basic-memory:share`).

## What you actually get

Once installed and set up (`/basic-memory:setup`):

- **Session briefings** — start each session knowing your active tasks, open decisions,
  and (if on a team) recent team context.
- **Checkpoints that survive compaction** — long sessions don't lose their thread.
- **Capture reflexes** — Claude searches before answering recall questions and writes
  down real decisions as it goes, citing permalinks.
- **Quick capture** — `/basic-memory:remember` for a fast note without breaking flow.
- **Team memory** — read across shared projects; publish back deliberately.

All of it in plain Markdown files you own, in projects you control — local, cloud, or
both.

## What it deliberately does *not* do

- It doesn't replace Claude's auto-memory or fight it — they run side by side.
- It doesn't capture *everything*. Auto-memory already handles the running summary;
  Basic Memory is for what's worth keeping. The plugin captures decisions and
  checkpoints, not every turn.
- It never auto-writes to a shared team project. Capture stays personal; sharing is a
  deliberate, confirmed gesture.

See [getting-started.md](./getting-started.md) to set it up, or
[architecture.md](./architecture.md) for how it works under the hood.
