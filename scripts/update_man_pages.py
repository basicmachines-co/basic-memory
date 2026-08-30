"""Regenerate the registry-owned sections of the bundled manual.

The MCP SYNOPSIS block on every section-3 page whose tool this build registers is
mechanical: it must show exactly the call the tool schema advertises. This script
renders those blocks from the live registry (``mcp.list_tools()``) and rewrites
them in place, flipping the page's ``generated:`` field to ``registry`` so the
ownership split is declared. Curated sections — DESCRIPTION, PARAMETERS,
EXAMPLES, GOTCHAS, SEE ALSO — are never touched.

Run after changing any MCP tool signature:

    just man-regen        (or: uv run python scripts/update_man_pages.py)

A test (tests/test_man_pages.py) holds every shipped block byte-equal to the
rendering, so a forgotten run fails CI with a pointer here.
"""

from __future__ import annotations

import asyncio
import re

from basic_memory.man import bundled_pages, render_synopsis, replace_mcp_synopsis
from basic_memory.mcp.server import mcp
import basic_memory.mcp.tools  # noqa: F401  (importing registers the tools)


async def main() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}
    changed: list[str] = []
    for page in bundled_pages():
        # Pages for tools this build does not register (hosted-only ones like
        # cloud_info) stay hand-owned: there is no schema here to render from.
        if page.section != 3 or page.tool not in tools:
            continue
        text = page.read()
        updated = replace_mcp_synopsis(
            text, render_synopsis(page.tool, tools[page.tool].parameters)
        )
        updated = re.sub(r"^generated: hand$", "generated: registry", updated, count=1, flags=re.M)
        if updated != text:
            page.path.write_text(updated, encoding="utf-8")
            changed.append(page.title)
    if changed:
        print(f"updated {len(changed)} page(s): {', '.join(changed)}")
    else:
        print("all pages already match the registry")


if __name__ == "__main__":
    asyncio.run(main())
