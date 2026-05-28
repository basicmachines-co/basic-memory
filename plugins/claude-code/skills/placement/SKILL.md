---
name: placement
description: Decide where a new note belongs in a Basic Memory project — which folder, matching project conventions read from a unified config file (basic-memory.md). Triggered automatically by a PreToolUse hook matching any MCP basic-memory write_note tool.
---

# Placement

Decides the `directory` parameter for a Basic Memory `write_note` call before it runs. Reads project and global config (`basic-memory.md`), then applies a short-circuit decision flow.

## When to Use

This skill is invoked automatically by a `PreToolUse` hook (matcher: `mcp__.*__write_note`) that catches any MCP basic-memory variant — local, cloud, or claude.ai connector. You can also invoke it directly when planning a write.

Inputs: the note's title and content (already drafted), and the active project name.

## Steps (stop at first match)

1. **Read configs** (project then global; reuse if cached in this conversation).
   - Project: `read_note(project, "basic-memory")` — extract `## Placements` section if present
   - Global: `~/.basic-memory/basic-memory.md` — extract `## Placements`. Look for `### <project-name>` first, then bare content under the H2.
2. **If config gives a definitive answer** → use it. Stop.
3. **List the project tree** via `list_directory`. If a folder is a clear topic match → use it. Stop.
4. **Follow precedent.** If similar notes already live at a specific location (root or a folder) — even if no folder name is a perfect topic match — place the new note there. Use `search_notes` to find the precedent if needed. Stop.
5. **Only ask the user if there is no config rule, no clear topic-matching folder, AND no precedent.** Don't ask just because nothing is a perfect topic match — precedent is enough.

## Defaults (apply when no rule speaks)

- Match by topic against existing folders.
- Follow precedent: if similar notes already live at a location, place there without asking.
- Never create new folders silently. Ask before creating.
- Avoid catch-all folders (`misc/`, `notes/`, `tmp/`) unless they already exist.
- Match the project's existing depth and naming convention.
- Never use date-based or type-based folders unless the project already does.

## Caching

If you have already read the project's `basic-memory` note or `~/.basic-memory/basic-memory.md` earlier in this conversation, reuse what you have. Re-read only after a known config change in the conversation (e.g., the user just edited it).

## Output

Set the `directory` parameter on the pending `write_note` call. If the project's naming convention differs from the proposed slug, also adjust the `title` to match.

If placement is ambiguous (multiple plausible folders, or no fit), ask the user before proceeding. Do not guess.

## Scope

This skill decides **where** a note goes. It does not:
- Decide whether to write the note (that's a separate concern)
- Redirect to `edit_note` for updates (placement only sets `directory` on `write_note`)
- Validate format or schema (those are separate concerns)
- Create folders silently

## Config file reference

The unified `basic-memory.md` config file uses this structure. H2 sections are categories. H3 sub-sections under an H2 are project-specific overrides; bare content under an H2 is the default for that category.

```markdown
# Basic Memory config

## Projects
- work: default project for daily work
- personal: personal notes and reflections
- research: long-form research notes

## Placements
- Place into existing folders by topic match
- Never create new top-level folders without asking
- Match the project's existing naming convention
- Default depth: 1-2 levels; avoid deeper unless content demands it

### research
- Long-form notes go in `papers/`
- Quick references go in `refs/`
- Reading lists go in `reading-lists/`

## Formats
- Required frontmatter: title, type, date
- Observation categories: fact, decision, technique, problem, solution

## Schemas
### work
person:
  - name
  - email
  - role

project:
  - name
  - status
  - owner
```

For more context on the config schema, see `PLUGIN.md`.
