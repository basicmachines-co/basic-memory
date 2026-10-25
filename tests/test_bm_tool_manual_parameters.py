"""Every public bm_* parameter must appear in its bundled manual."""

import ast
import inspect
import re

import pytest

from basic_memory.man import PageRef, extract_mcp_synopsis, extract_parameters, find_page
from basic_memory.mcp import tools


@pytest.mark.parametrize("name", [name for name in tools.__all__ if name.startswith("bm_")])
def test_bm_manual_lists_every_tool_parameter(name: str) -> None:
    # Inspect the exported function rather than the visible registry: optional tools
    # such as project deletion must stay documented even when hidden at startup.
    parameters = set(inspect.signature(getattr(tools, name)).parameters) - {"context"}
    page = find_page(PageRef(name.replace("_", "-"), 3))
    assert page is not None, f"{name} has no manual page"
    text = page.read()
    documented = set(re.findall(r"^- \*\*(\w+)\*\*", extract_parameters(text), re.MULTILINE))
    synopsis = ast.parse(f"def {extract_mcp_synopsis(text)}: pass").body[0]
    assert isinstance(synopsis, ast.FunctionDef)
    listed = {arg.arg for arg in synopsis.args.args + synopsis.args.kwonlyargs}
    assert documented == parameters, f"{name} PARAMETERS drifted; run `just man-regen`"
    assert listed == parameters, f"{name} SYNOPSIS drifted; run `just man-regen`"
