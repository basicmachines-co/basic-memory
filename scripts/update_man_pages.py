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

A test (tests/test_man_pages.py) holds every shipped block byte-equal to the
rendering, so a forgotten run fails CI with a pointer here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import click
import typer.main

from basic_memory.man import (
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
from basic_memory.mcp.server import mcp
import basic_memory.mcp.tools  # noqa: F401  (importing registers the tools)

# Importing the command modules runs their @app.command()/@app.add_typer
# decorators, which is what populates the Typer command tree. The `bm --version`
# fast path in cli/main.py skips these imports, so a script that walks the tree
# must import them explicitly or every subcommand comes back empty.
from basic_memory.cli.app import app
import basic_memory.cli.commands.posix  # noqa: F401  (registers cat/grep/ls/find/tail/head/tree)
import basic_memory.cli.commands.man  # noqa: F401  (registers `bm man apropos`)

# Section-1 page name -> `bm` command path. Seven pages resolve directly from the
# page name (`grep` -> `bm grep`); apropos(1) documents `bm man apropos`, a verb on
# the `man` subgroup, so it needs an explicit path. The map lives here rather than
# in page frontmatter because man1/*.md is only ever rewritten by this generator.
SECTION1_COMMAND_PATHS: Mapping[str, str] = {"apropos": "man apropos"}


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


async def main() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}
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
