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

The check validates every `memory-*/SKILL.md` (name format, a description of at
most 200 characters with no `<` or `>`, and that files it links to exist), checks
that the skill lists in `README.md`, `CLAUDE.md`, and the OpenClaw plugin match the
skill directories, and builds every release archive into a temp dir.

### Internal skills

A skill meant only for tooling (today, `memory-ci-capture`) carries this in its
frontmatter:

```yaml
metadata:
  internal: true
```

The [Skills CLI](https://github.com/vercel-labs/skills) hides it unless
`INSTALL_INTERNAL_SKILLS=1` is set, and the release build, the skill-list check,
and OpenClaw's `fetch-skills` leave it out.

## Installing via npx

Users can install or update skills with the [Skills CLI](https://github.com/vercel-labs/skills):

```bash
# Install all skills
npx skills add basicmachines-co/basic-memory/skills -g

# Install a specific skill
npx skills add basicmachines-co/basic-memory/skills -g --skill memory-tasks

# Install for a specific agent
npx skills add basicmachines-co/basic-memory/skills -g --agent claude-code
```

## Adding a New Skill

1. Create a new directory: `memory-<name>/SKILL.md`
2. Add YAML frontmatter with `name` and `description`
3. Write skill instructions in markdown
4. Update `README.md` with the new skill's summary
5. Commit and push

## Packaging for Distribution

`just dist` (from `skills/`) validates the skills, then zips them into
`dist/` (at the monorepo root) in two flavors:

```
dist/skills/<name>.zip                              # Agent Skills format (SKILL.md + resources)
dist/skills/basic-memory.zip                        # one combined skill (see below)
dist/skills/basic-memory-skills.zip                 # every skill, for unzipping by hand
dist/skills-openai/<name>-chatgpt.zip               # same skill + agents/openai.yaml
dist/skills-openai/basic-memory-chatgpt.zip
dist/skills-openai/basic-memory-skills-chatgpt.zip
```

Each zip contains a `<name>/SKILL.md` folder at its root, so unzipping (or
dropping it into an uploader) lands a valid skill directory — the layout the
[Agent Skills spec](https://agentskills.io/specification), Claude, and ChatGPT
all expect. `evals/` folders are left out, and internal skills are not packaged.
The build checks each zip's layout and fails if two assets share a name.
`dist/` is gitignored; the archives are build artifacts, so the `skills/` source
stays pure markdown.

On every push to `main` that touches the skills, `.github/workflows/publish-skills.yml`
uploads every archive to the rolling `skills-latest` release. The Basic Memory web
app links to `basic-memory.zip`, `basic-memory-chatgpt.zip`, and
`memory-onboarding.zip` there by name, so don't rename them.

### The combined `basic-memory` skill

Claude and ChatGPT take one skill per upload, so `basic-memory.zip` folds the
skills into one. Its router `SKILL.md` comes from
`scripts/skill-bundle/basic-memory.md` (named so no skill loader discovers it as
a second copy of these skills); the build fills in its routing table and writes
each skill's `SKILL.md` to `references/<name>.md`, with its resources under
`references/<name>/` and links rewritten to match. A new `memory-*` skill joins
it automatically. `memory-defrag`, `memory-reflect`, and
`memory-literary-analysis` are left out because they need a local install (see
`COMBINED_EXCLUDED` in `scripts/build_skills_dist.py`).

### ChatGPT / Codex (openai.yaml)

A ChatGPT/Codex skill is the *same* SKILL.md plus an optional
[`agents/openai.yaml`](https://learn.chatgpt.com/docs/build-skills) holding
OpenAI-specific display metadata. `just dist` generates that file for each skill
from the SKILL.md frontmatter (`interface.display_name`, `short_description`,
`brand_color`) — see `scripts/build_skills_dist.py`. To hand-tune a skill, add a
source `skills/<name>/agents/openai.yaml`; the builder copies it verbatim
into the `-chatgpt` zip instead of generating one (the plain zip leaves `agents/` out).

No MCP dependency is pinned in `openai.yaml`: these skills need the basic-memory
MCP server, but that is wired at the host/plugin level (the ChatGPT plugin
builder's **MCP** step, or a local `.mcp.json`), not per skill.

## OpenClaw Plugin Integration

These skills are also bundled in the [`@basicmemory/openclaw-basic-memory`](../integrations/openclaw) plugin. When updating skills here, refresh the generated OpenClaw bundle:

```bash
# From integrations/openclaw
bun run fetch-skills
```

Then add any new path to the `skills` array in `integrations/openclaw/openclaw.plugin.json` and commit the monorepo change.
