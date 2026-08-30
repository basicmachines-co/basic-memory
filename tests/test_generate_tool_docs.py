"""Tests for scripts/generate_tool_docs.py, the MCP tool reference generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_generate_tool_docs() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_tool_docs.py"
    spec = importlib.util.spec_from_file_location("generate_tool_docs", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load generator script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    # The script's dataclasses resolve their (string) annotations through
    # sys.modules[module.__name__], so it has to be registered before executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generate_tool_docs = load_generate_tool_docs()
Tool = generate_tool_docs.Tool


def test_render_disambiguates_category_and_tool_anchors() -> None:
    # The "Search" category and the `search` tool both slug to "search". GitHub keeps the
    # first heading bare and suffixes the second, so the tool's TOC link must follow suit
    # or it lands on the category heading instead of the tool.
    tools = [Tool(name="search", summary="Adapter.", description="", source_file="x.py")]

    doc = generate_tool_docs._render(tools)

    assert "- [Search](#search)\n" in doc
    assert "  - [`search`](#search-1)\n" in doc


def test_render_nests_docstring_headings_under_tool_heading() -> None:
    # A docstring may carry its own `##`/`###` structure (search_notes does). Emitted
    # verbatim under the `###` tool heading it would end the tool's section, so the
    # shallowest embedded heading must land one level below the tool heading. Fenced
    # code is not Markdown structure and stays untouched.
    description = "Intro.\n\n## Syntax\n\n### Basics\n- item\n\n```\n# shell comment\n```"
    tools = [
        Tool(name="search_notes", summary="Search.", description=description, source_file="x.py")
    ]

    doc = generate_tool_docs._render(tools)

    assert "### `search_notes`\n" in doc
    assert "\n#### Syntax\n" in doc
    assert "\n##### Basics\n" in doc
    assert "\n# shell comment\n" in doc
    assert "\n## Syntax\n" not in doc


def test_nested_docstring_headings_count_toward_anchor_disambiguation() -> None:
    # Anchors are handed out in document order. write_note renders under the first
    # category, so a "Diagnostics" heading in its docstring claims "#diagnostics" before
    # the later "Diagnostics" category does, and the category's TOC link must say so.
    tools = [
        Tool(name="write_note", summary="W.", description="## Diagnostics", source_file="x.py"),
        Tool(name="basic_memory_diagnostics", summary="D.", description="", source_file="x.py"),
    ]

    doc = generate_tool_docs._render(tools)

    assert "\n#### Diagnostics\n" in doc
    assert "- [Diagnostics](#diagnostics-1)\n" in doc


def test_generator_documents_every_registered_tool_deterministically() -> None:
    tools = generate_tool_docs._collect_tools()

    assert sorted(tool.name for tool in tools) == sorted(
        generate_tool_docs._load_registered_tool_names()
    )
    assert generate_tool_docs._render(tools) == generate_tool_docs._render(
        generate_tool_docs._collect_tools()
    )
