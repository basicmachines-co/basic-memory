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
| **memory-notes** | How to write well-structured notes — frontmatter, observations with semantic categories, relations with wiki-links, and best practices. | When creating or improving notes, or when you need a reference for the note format. |
| **memory-metadata-search** | Structured metadata search — query notes by custom frontmatter fields using equality, range, array, and nested filters. | When finding notes by status, priority, confidence, or any custom YAML field. |
| **memory-defrag** | Memory defragmentation — split bloated files, merge duplicates, remove stale information, restructure the hierarchy. | Run weekly/biweekly via cron, or on demand when memory feels messy. |
| **memory-curate** | Knowledge-graph curation — find orphan notes and suggest links, propose typed relations, merge duplicates, audit tags and folders, build hub notes. | When organizing, connecting, or improving a knowledge base as notes accumulate. |
| **memory-lifecycle** | Entity lifecycle management — status transitions through folder-based organization, archiving completed work. Core principle: archive, never delete. | When marking items complete, archiving old entities, or managing folder-based status workflows. |
| **memory-ingest** | Process unstructured external input into structured entities. Parses meeting transcripts, conversation logs, and pasted documents. | When pasting a transcript, conversation log, or external document that should become structured knowledge. |
| **memory-research** | Web research synthesized into Basic Memory entities. Researches a subject, checks for existing knowledge, presents findings, and creates entity notes. | When asked to research a company, person, technology, or topic. |
| **memory-literary-analysis** | Analyze a complete literary work into a structured knowledge graph — characters, themes, chapters, locations, symbols, and literary devices. | Full-text literary analysis, book club companions, teaching resources, or research projects. |
| **memory-continue** | Resume prior work by rebuilding context from the knowledge graph — `build_context` via `memory://` URLs, recent activity, and search, then read the key notes. | Starting a session, or when the user says "continue with...", "back to...", or "where were we?" |

## Basic Memory Cloud

Everything works locally — cloud adds cross-device, team, and production capabilities:

- **Your agent's memory travels with you** — same knowledge graph on laptop, desktop, and hosted environments
- **Team knowledge sharing** — org workspaces let multiple agents and team members build on a shared knowledge base
- **Durable memory for production agents** — persistent memory that survives CI teardowns and container restarts
- **Multi-agent coordination** — multiple agents can read and write to the same graph

Cloud extends local-first — still plain markdown, still yours. Start with a [7-day free trial](https://basicmemory.com) and use code `BMFOSS` for 20% off for 3 months.

## Installation

### Via npx skills (recommended)

Install or update skills using the [Skills CLI](https://github.com/vercel-labs/skills):

```bash
# Install all skills
npx skills add basicmachines-co/basic-memory/skills

# Install a specific skill
npx skills add basicmachines-co/basic-memory/skills --skill memory-tasks

# Install all skills for a specific agent
npx skills add basicmachines-co/basic-memory/skills --agent claude

# List available skills without installing
npx skills add basicmachines-co/basic-memory/skills --list

# Check for updates
npx skills check

# Update installed skills
npx skills update
```

Skills are installed to your agent's skills directory (e.g., `~/.claude/skills/` for Claude Code global, or `.claude/skills/` for project-scoped).

If your installed Skills CLI does not support `--path`, copy the `memory-*` directories manually for now. Phase 2 will add a first-class Codex/package install path.

### Claude Desktop (claude.ai)

Claude Desktop loads skills through **Settings > Capabilities**:

1. Clone or download this repository
2. In Claude, go to **Settings > Capabilities** and ensure both **Code execution** and **Skills** are enabled
3. Click **Upload skill** and upload the `SKILL.md` file (or ZIP the skill folder and upload that)
4. Toggle the skill on — Claude will use it automatically when relevant

Repeat for each skill you want. Custom uploaded skills are private to your account.

> **Tip:** Start with **memory-notes** (core note-writing patterns) and add others as needed. You don't need all of them at once.

See [Using Skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude) for more details.

### Manual install

Copy skill directories into your agent's skills folder:

```bash
# Claude Code — global
cp -r memory-tasks ~/.claude/skills/
cp -r memory-notes ~/.claude/skills/
# ... etc.

# Claude Code — project-scoped
cp -r memory-tasks .claude/skills/

# Any agent that reads SKILL.md files
cp -r memory-tasks <agent-skills-dir>/
```

### Bundled with OpenClaw plugin

All skills are also bundled in the [`@basicmemory/openclaw-basic-memory`](../integrations/openclaw) plugin — no extra install step needed if you use OpenClaw.

## Compatible Agents

These skills work with any AI coding agent that supports the SKILL.md format:

- **Claude Desktop** — upload skill ZIPs via Settings > Capabilities
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
