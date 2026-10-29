"""Regenerate the generator-owned sections of the bundled manual.

Two section families carry mechanical blocks that must track a source of truth:

- **Section 3** (one page per MCP tool): the MCP SYNOPSIS and PARAMETERS blocks
  must show exactly what the tool schema advertises. They are rendered from the
  live registry (``mcp.list_tools()``) and the page's ``generated:`` field is set
  to ``registry``.
- **Section 1** (one page per ``bm`` verb): the shell SYNOPSIS and OPTIONS blocks
  must show exactly what the Typer command tree advertises. They are rendered from
  the resolved Click command and the page's ``generated:`` field is set to ``cli``.

Curated sections — DESCRIPTION, EXAMPLES, GOTCHAS, SEE ALSO, and every other
hand-owned block — are never touched.

Run after changing any MCP tool signature or ``bm`` verb option:

    just man-regen        (or: uv run python scripts/update_man_pages.py)

A new MCP tool gets its page scaffolded from the registry, then curated by hand:

    uv run python scripts/update_man_pages.py --new bm_read bm_write

A test (tests/test_man_pages.py) holds every shipped block byte-equal to the
rendering, so a forgotten run fails CI with a pointer here.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from typing import Any

import click
import typer.main
import yaml

from basic_memory import __version__
from basic_memory.man import (
    MAN_DIR,
    bundled_pages,
    declare_ownership,
    declare_registry_ownership,
    remove_parameters,
    render_cli_synopsis,
    render_options,
    render_parameters,
    render_synopsis,
    replace_cli_synopsis,
    replace_mcp_synopsis,
    replace_options,
    replace_parameters,
)
from basic_memory.mcp.server import mcp, set_project_delete_visibility
import basic_memory.mcp.tools  # noqa: F401  (importing registers the tools)

# Importing the command modules runs their @app.command()/@app.add_typer
# decorators, which is what populates the Typer command tree. The `bm --version`
# fast path in cli/main.py skips these imports, so a script that walks the tree
# must import them explicitly or every subcommand comes back empty.
from basic_memory.cli.app import app
import basic_memory.cli.commands.posix  # noqa: F401  (registers cat/grep/ls/find/head/tree)
import basic_memory.cli.commands.okf  # noqa: F401 (registers OKF commands)
import basic_memory.cli.commands.man  # noqa: F401  (registers `bm man apropos`)

# Section-1 page name -> `bm` command path. Seven pages resolve directly from the
# page name (`grep` -> `bm grep`); apropos(1) documents `bm man apropos`, a verb on
# the `man` subgroup, so it needs an explicit path. The map lives here rather than
# in page frontmatter because man1/*.md is only ever rewritten by this generator.
SECTION1_COMMAND_PATHS: Mapping[str, str] = {
    "apropos": "man apropos",
    "okf-export": "okf export",
    "okf-check": "okf check",
}


def resolve_cli_command(command_path: str) -> Any:
    """Walk the Typer/Click command tree to the command a page documents.

    ``command_path`` is space-separated (``man apropos``); each segment resolves
    through its parent group's Click context, the shape Click's ``get_command``
    requires. Only non-leaf segments are resolved this way, so ``get_command`` is
    never called on a leaf command. Typer vendors its own Click, so the resolved
    objects are not instances of the top-level ``click`` classes; ``Any`` is the
    honest type.
    """
    command: Any = typer.main.get_command(app)
    ctx = click.Context(command, info_name="bm")
    for segment in command_path.split():
        resolved = command.get_command(ctx, segment)
        if resolved is None:
            raise ValueError(f"no such command: bm {command_path}")
        command = resolved
        ctx = click.Context(command, info_name=segment, parent=ctx)
    return command


def regenerate_page(text: str, tool_name: str, schema: Mapping[str, Any]) -> str:
    """Rewrite the registry-owned sections of one section-3 page from its schema.

    SYNOPSIS is always mechanical. PARAMETERS exists exactly when the schema has
    properties: a tool with parameters gets the rendered block (rewritten in place
    or inserted), and a parameterless tool gets none — any previously generated
    block is stripped so the page never advertises removed arguments. Ownership is
    then declared by flipping ``generated:`` to ``registry``.
    """
    updated = replace_mcp_synopsis(text, render_synopsis(tool_name, schema))
    if schema.get("properties"):
        updated = replace_parameters(updated, render_parameters(tool_name, schema))
    else:
        updated = remove_parameters(updated)
    return declare_registry_ownership(updated)


def regenerate_cli_page(text: str, command_path: str, command: Any) -> str:
    """Rewrite the CLI-owned sections of one section-1 page from its Click command.

    Both SYNOPSIS (the shell form) and OPTIONS are mechanical restatements of the
    command's parameters, so both are rendered and replaced in place; ownership is
    then declared by flipping ``generated:`` to ``cli``.
    """
    updated = replace_cli_synopsis(text, render_cli_synopsis(command_path, command))
    updated = replace_options(updated, render_options(command))
    return declare_ownership(updated, owner="cli")


def scaffold_page(tool_name: str, description: str, schema: Mapping[str, Any]) -> str:
    """A new section-3 page for a registered tool, ready for curation.

    The page name is the tool name with hyphens (bm_read -> bm-read). SYNOPSIS and
    PARAMETERS come from the schema, as regeneration would write them; NAME takes the
    description's first sentence and DESCRIPTION the rest of it. EXAMPLES and SEE
    ALSO are left for the author, since only verified calls belong there.
    """
    name = tool_name.replace("_", "-")
    summary, _, rest = description.strip().partition(". ")
    summary = summary.rstrip(".")
    lowered = summary[:1].lower() + summary[1:]
    sections = [
        "---",
        f"title: {name}(3)",
        "type: manpage",
        "section: 3",
        f"name: {name}",
        # A summary may hold ': ', which plain YAML would read as a mapping.
        f"summary: {yaml.safe_dump(lowered, width=1_000).removesuffix(chr(10)).removesuffix('...').strip()}",
        "generated: registry",
        f"tool: {tool_name}",
        f"verified: {__version__} mcp",
        "---",
        "",
        f"# {name}(3)",
        "",
        "## NAME",
        "",
        f"**{name}** — {lowered}",
        "",
        "## SYNOPSIS",
        "",
        "```",
        render_synopsis(tool_name, schema),
        "```",
        "",
        "## DESCRIPTION",
        "",
        rest.strip() or f"{summary}.",
        "",
    ]
    if schema.get("properties"):
        sections += ["## PARAMETERS", "", render_parameters(tool_name, schema), ""]
    sections += ["## EXAMPLES", "", "```", "```", "", "## SEE ALSO", ""]
    return "\n".join(sections)


def scaffold(tools: Mapping[str, Any], tool_names: list[str]) -> None:
    """Write a new page for each named tool; an existing page is never overwritten."""
    documented = {page.tool for page in bundled_pages() if page.section == 3}
    for tool_name in tool_names:
        if tool_name not in tools:
            raise SystemExit(f"no registered tool named {tool_name}")
        if tool_name in documented:
            print(f"{tool_name} already has a page; left as is")
            continue
        tool = tools[tool_name]
        name = tool_name.replace("_", "-")
        path = MAN_DIR / "man3" / f"{name}(3).md"
        path.write_text(
            scaffold_page(tool_name, tool.description or "", tool.parameters), encoding="utf-8"
        )
        print(f"created {path.relative_to(MAN_DIR)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--new", nargs="+", metavar="TOOL", help="scaffold section-3 pages for these tools"
    )
    arguments = parser.parse_args()
    # The manual documents every tool, including the project-delete tools that a
    # server shows only when enable_project_delete is on.
    set_project_delete_visibility(mcp, True)
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}
    if arguments.new:
        scaffold(tools, arguments.new)
        # bundled_pages() is cached; a scaffold run ends here so the next
        # regeneration reads the new pages fresh.
        return
    changed: list[str] = []
    for page in bundled_pages():
        text = page.read()
        if page.section == 3:
            # Pages for tools this build does not register (hosted-only ones like
            # cloud_info) stay hand-owned: there is no schema here to render from.
            if page.tool not in tools:
                continue
            updated = regenerate_page(text, page.tool, tools[page.tool].parameters)
        elif page.section == 1:
            command_path = SECTION1_COMMAND_PATHS.get(page.name, page.name)
            updated = regenerate_cli_page(text, command_path, resolve_cli_command(command_path))
        else:
            continue
        if updated != text:
            page.path.write_text(updated, encoding="utf-8")
            changed.append(page.title)
    if changed:
        print(f"updated {len(changed)} page(s): {', '.join(changed)}")
    else:
        print("all pages already match their source")


if __name__ == "__main__":
    asyncio.run(main())
