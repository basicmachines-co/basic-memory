# Basic Memory Skills

Skills for [Basic Memory](https://github.com/basicmachines-co/basic-memory) — teach AI coding agents how to use Basic Memory's MCP tools effectively.

This is the canonical source for Basic Memory `SKILL.md` files. The former `basic-memory-skills` repository is now a satellite distribution target; ongoing development happens here under `skills/`.

## What Are Skills?

Skills are markdown instruction files (`SKILL.md`) that AI agents load for domain-specific guidance. Each skill contains structured knowledge about *when* and *how* to use specific tools, with examples and best practices.

Basic Memory provides the MCP server — tools like `write_note`, `search_notes`, and `build_context` for managing a local-first knowledge graph. These skills teach agents how to use those tools well: when to create tasks vs. notes, how to structure observations for searchability, when to run schema validation, and more.

## Skills

| Skill | Description | When to use |
|-------|-------------|-------------|
| **memory-tasks** | Structured task tracking that survives context compaction. Creates typed `Task` notes with steps, status, and context. | Multi-step work (3+ steps), anything that might outlast a context window, or after compaction to resume. |
| **memory-schema** | Schema lifecycle management — discover unschemaed notes, infer schemas, create/edit definitions, validate, and detect drift. | When structured note types emerge (Task, Person, Meeting, etc.) and you want consistency. |
| **memory-reflect** | Sleep-time memory reflection. Reviews recent conversations and daily notes, extracts insights, consolidates into long-term memory. Inspired by [sleep-time compute](https://www.letta.com/blog/sleep-time-compute). | Schedule via cron (1-2x daily), trigger from heartbeat, or run on demand. |
| **memory-capture** | Capture the current state of a working thread into a single coherent note — synthesize where it landed, not an append-log. Re-captures rewrite the same note in place via a `thread_id` key. | Mid-thread or end-of-thread, when decisions, insights, or context are worth preserving. |
| **memory-comark** | Charts, tables, checklists, kanban boards, and live views over the knowledge base written as `::bm-*` Comark components. Basic Memory Cloud draws them; everywhere else the Markdown stays readable. | When a note reads better as a figure or board, when it should show live counts or rankings from the project, or when editing a note that already has `::bm-*` directives. |
| **memory-notes** | How to write well-structured notes — frontmatter, observations with semantic categories, relations with wiki-links, and best practices. | When creating or improving notes, or when you need a reference for the note format. |
| **memory-metadata-search** | Structured metadata search — query notes by custom frontmatter fields using equality, range, array, and nested filters. | When finding notes by status, priority, confidence, or any custom YAML field. |
| **memory-defrag** | Memory defragmentation — split bloated files, merge duplicates, remove stale information, restructure the hierarchy. | Run weekly/biweekly via cron, or on demand when memory feels messy. |
| **memory-curate** | Knowledge-graph curation — find orphan notes and suggest links, propose typed relations, merge duplicates, audit tags and folders, build hub notes. | When organizing, connecting, or improving a knowledge base as notes accumulate. |
| **memory-lifecycle** | Entity lifecycle management — status transitions through folder-based organization, archiving completed work. Core principle: archive, never delete. | When marking items complete, archiving old entities, or managing folder-based status workflows. |
| **memory-ingest** | Process unstructured external input into structured entities. Parses meeting transcripts, conversation logs, and pasted documents. | When pasting a transcript, conversation log, or external document that should become structured knowledge. |
| **memory-research** | Web research synthesized into Basic Memory entities. Researches a subject, checks for existing knowledge, presents findings, and creates entity notes. | When asked to research a company, person, technology, or topic. |
| **memory-literary-analysis** | Analyze a complete literary work into a structured knowledge graph — characters, themes, chapters, locations, symbols, and literary devices. | Full-text literary analysis, book club companions, teaching resources, or research projects. |
| **memory-continue** | Resume prior work by rebuilding context from the knowledge graph — `build_context` via `memory://` URLs, recent activity, and search, then read the key notes. | Starting a session, or when the user says "continue with...", "back to...", or "where were we?" |
| **memory-onboarding** | Guided onboarding for people new to Basic Memory — interview, blueprint, approval gate, then build a full system: schemas, templates, instruction notes, a startup router, indexes, and real seed notes, plus assistant setup so the rules load every session. | When a user is new to Basic Memory, doesn't know what to use it for, wants structure in an empty or messy project, or wants their assistant to follow consistent rules across sessions. |

`memory-research` asks the agent to search the web. Basic Memory does not ship that tool. If the host already has web search, keep using it. If not, add any web search tool or MCP server, for example [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp). The search provider receives the agent's queries, so keep private note content out of them. The agent still asks before saving a note.

## Basic Memory Cloud

Everything works locally — cloud adds cross-device, team, and production capabilities:

- **Your agent's memory travels with you** — same knowledge graph on laptop, desktop, and hosted environments
- **Team knowledge sharing** — org workspaces let multiple agents and team members build on a shared knowledge base
- **Durable memory for production agents** — persistent memory that survives CI teardowns and container restarts
- **Multi-agent coordination** — multiple agents can read and write to the same graph

Cloud extends local-first — still plain markdown, still yours. Start with a [7-day free trial](https://basicmemory.com) and use code `BMFOSS` for 20% off for 3 months.

## Installation

### Claude Code, Codex, Cursor, and other coding agents

Install or update every skill with the [Skills CLI](https://github.com/vercel-labs/skills). `-g` installs at user level, so the skills are available in every project:

```bash
# Install all skills for every agent the CLI detects
npx skills add basicmachines-co/basic-memory/skills -g

# Install a specific skill
npx skills add basicmachines-co/basic-memory/skills -g --skill memory-tasks

# Install for a specific agent
npx skills add basicmachines-co/basic-memory/skills -g --agent claude-code

# List available skills without installing
npx skills add basicmachines-co/basic-memory/skills --list

# Update installed skills
npx skills update
```

Leave off `-g` to install into the current project instead (for example `.claude/skills/`). Start a new session afterwards, then invoke a skill by name — `/memory-tasks` in Claude Code, `$memory-tasks` in Codex — or just describe the task and let the agent pick it up.

If your installed Skills CLI cannot load `basicmachines-co/basic-memory/skills`, update the CLI or copy the `memory-*` directories manually (below).

### Claude and ChatGPT: upload a zip

Chat apps take skills as uploaded zips, one skill per upload. Every build of `main` publishes them to the rolling [`skills-latest`](https://github.com/basicmachines-co/basic-memory/releases/tag/skills-latest) release, at `https://github.com/basicmachines-co/basic-memory/releases/download/skills-latest/<asset>`:

| Asset | Upload to |
|---|---|
| [`basic-memory.zip`](https://github.com/basicmachines-co/basic-memory/releases/download/skills-latest/basic-memory.zip) | **Recommended for Claude.** One skill that routes to the others, which it carries as references. |
| [`basic-memory-chatgpt.zip`](https://github.com/basicmachines-co/basic-memory/releases/download/skills-latest/basic-memory-chatgpt.zip) | **Recommended for ChatGPT.** The same skill with ChatGPT metadata. |
| `<skill>.zip`, e.g. [`memory-onboarding.zip`](https://github.com/basicmachines-co/basic-memory/releases/download/skills-latest/memory-onboarding.zip) | One individual skill, for Claude. |
| `<skill>-chatgpt.zip`, e.g. `memory-onboarding-chatgpt.zip` | One individual skill, for ChatGPT. |
| `basic-memory-skills.zip`, `basic-memory-skills-chatgpt.zip` | Every individual skill in one archive, for unzipping by hand. Don't upload these. |

The combined `basic-memory` skill leaves out `memory-defrag`, `memory-reflect`, and `memory-literary-analysis`, which need a local Basic Memory install; their individual zips are still published. Either way, the skills need the Basic Memory connector (or MCP server) connected in the same app.

**Claude (claude.ai and Claude Desktop):**

1. Turn on **Code execution** in **Settings → Capabilities** — skills need it.
2. Go to **Customize → Skills**, click **+**, choose **Create skill**, then **Upload a skill**.
3. Upload `basic-memory.zip` (or one `<skill>.zip`), and make sure the skill is toggled on.

Uploaded skills are private to your account. See [Using Skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude).

**ChatGPT** (Business, Enterprise, Healthcare, and Edu workspaces where an admin has turned on **Enable skill uploading**):

1. Open **Plugins** and switch to the **Skills** tab.
2. Choose **Create**, then **Upload from your computer**.
3. Upload `basic-memory-chatgpt.zip` (or one `<skill>-chatgpt.zip`). Use the `-chatgpt` zips here: they add the `agents/openai.yaml` card ChatGPT displays.

### Manual install

Copy skill directories into your agent's skills folder:

```bash
# Claude Code — global
cp -r memory-tasks ~/.claude/skills/

# Claude Code — project-scoped
cp -r memory-tasks .claude/skills/

# Any agent that reads SKILL.md files
cp -r memory-tasks <agent-skills-dir>/
```

### Bundled with OpenClaw plugin

All skills are also bundled in the [`@basicmemory/openclaw-basic-memory`](../integrations/openclaw) plugin — no extra install step needed if you use OpenClaw.

## Compatible Agents

These skills work with any AI coding agent that supports the SKILL.md format:

- **Claude (claude.ai, Claude Desktop)** and **ChatGPT** — upload a zip from the `skills-latest` release
- **Codex** — loads skills installed by `npx skills add ... -g`
- **Claude Code** — loads skills from `~/.claude/skills/` or `.claude/skills/`
- **Cursor** — AI-powered coding with skill support
- **Windsurf** — agent-based development with skill loading
- **Any agent** supporting markdown-based skill files

## Development

See [DEVELOPMENT.md](./DEVELOPMENT.md) for testing and contribution details.

Quick validation:

```bash
# From the monorepo root
just package-check-skills

# From this directory
just check
```

## License

MIT
