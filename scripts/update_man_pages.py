"""Regenerate the registry-owned sections of the bundled manual.

The MCP SYNOPSIS and PARAMETERS blocks on every section-3 page whose tool this
build registers are mechanical: they must show exactly what the tool schema
advertises. This script renders those blocks from the live registry
(``mcp.list_tools()``) and rewrites them in place, flipping the page's
``generated:`` field to ``registry`` so the ownership split is declared. Curated
sections — DESCRIPTION, EXAMPLES, GOTCHAS, SEE ALSO — are never touched.

Run after changing any MCP tool signature:

    just man-regen        (or: uv run python scripts/update_man_pages.py)

A test (tests/test_man_pages.py) holds every shipped block byte-equal to the
rendering, so a forgotten run fails CI with a pointer here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from basic_memory.man import (
    bundled_pages,
    declare_registry_ownership,
    remove_parameters,
    render_parameters,
    render_synopsis,
    replace_mcp_synopsis,
    replace_parameters,
)
from basic_memory.mcp.server import mcp
import basic_memory.mcp.tools  # noqa: F401  (importing registers the tools)


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


async def main() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}
    changed: list[str] = []
    for page in bundled_pages():
        # Pages for tools this build does not register (hosted-only ones like
        # cloud_info) stay hand-owned: there is no schema here to render from.
        if page.section != 3 or page.tool not in tools:
            continue
        text = page.read()
        updated = regenerate_page(text, page.tool, tools[page.tool].parameters)
        if updated != text:
            page.path.write_text(updated, encoding="utf-8")
            changed.append(page.title)
    if changed:
        print(f"updated {len(changed)} page(s): {', '.join(changed)}")
    else:
        print("all pages already match the registry")


if __name__ == "__main__":
    asyncio.run(main())
