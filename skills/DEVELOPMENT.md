# Development

## Repository Structure

```
basic-memory/skills/
├── memory-tasks/SKILL.md           # Task tracking skill
├── memory-schema/SKILL.md          # Schema lifecycle skill
├── memory-reflect/SKILL.md         # Memory consolidation skill
├── memory-notes/SKILL.md           # Note writing patterns skill
├── memory-defrag/SKILL.md          # Memory cleanup skill
├── memory-metadata-search/SKILL.md # Metadata search skill
├── memory-lifecycle/SKILL.md       # Entity lifecycle skill
├── memory-ingest/SKILL.md          # External input processing skill
├── memory-research/SKILL.md        # Web research skill
├── memory-literary-analysis/SKILL.md # Literary analysis skill
└── README.md
```

Each skill is a single `SKILL.md` file with YAML frontmatter (`name`, `description`) and markdown instructions.

## Testing Skills Locally

Copy a skill into your agent's skills directory and start a new session:

```bash
# Claude Code — global
cp -r memory-tasks ~/.claude/skills/

# Claude Code — project-scoped
cp -r memory-tasks .claude/skills/

# Any agent that reads SKILL.md files
cp -r memory-tasks <agent-skills-dir>/
```

Then start a new session and verify the skill is loaded (e.g., ask the agent to create a task).

Run the source validation before committing skill changes:

```bash
# From the monorepo root
just package-check-skills

# From skills/
just check
```

The check validates every `memory-*/SKILL.md` frontmatter block. Skills are markdown-only, so there is no separate compile step.

## Installing via npx

Users can install or update skills with the [Skills CLI](https://github.com/vercel-labs/skills):

```bash
# Install all skills
npx skills add basicmachines-co/basic-memory/skills

# Install a specific skill
npx skills add basicmachines-co/basic-memory/skills --skill memory-tasks

# Install for a specific agent
npx skills add basicmachines-co/basic-memory/skills --agent claude
```

## Adding a New Skill

1. Create a new directory: `memory-<name>/SKILL.md`
2. Add YAML frontmatter with `name` and `description`
3. Write skill instructions in markdown
4. Update `README.md` with the new skill's summary
5. Commit and push

## OpenClaw Plugin Integration

These skills are also bundled in the [`@basicmemory/openclaw-basic-memory`](../integrations/openclaw) plugin. When updating skills here, refresh the generated OpenClaw bundle:

```bash
# From integrations/openclaw
bun run fetch-skills
```

Then add any new path to the `skills` array in `integrations/openclaw/openclaw.plugin.json` and commit the monorepo change.
