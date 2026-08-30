#!/usr/bin/env python3
"""Generate a comprehensive MCP tool reference for Basic Memory.

This script introspects the MCP tool source files under
``src/basic_memory/mcp/tools/`` using Python's ``ast`` module and emits a single
Markdown reference document at ``docs/mcp-tools.md``.

Design goals
------------
* **Zero runtime dependencies** — pure standard library (``ast``, ``pathlib``,
  ``textwrap``). The tools import heavy optional packages, so we never *import*
  them; we parse their source statically instead.
* **Source of truth is ``__all__``** — only tools that are actually registered in
  ``src/basic_memory/mcp/tools/__init__.py`` are documented. Internal helper
  functions are ignored.
* **Idempotent** — running the script twice produces a byte-for-byte identical
  file. Tools are emitted in a deterministic order.

Usage
-----
    uv run scripts/generate_tool_docs.py

Run from the repository root (or anywhere — paths are resolved relative to this
file). Targets Python 3.12+.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "src" / "basic_memory" / "mcp" / "tools"
OUTPUT_PATH = REPO_ROOT / "docs" / "mcp-tools.md"

# ---------------------------------------------------------------------------
# Logical grouping. Every registered tool maps to exactly one category; tools
# not listed fall back to "Other Tools". Category order here is the render order.
# ---------------------------------------------------------------------------

CATEGORY_ORDER: list[str] = [
    "Note Management",
    "Reading & Navigation",
    "Search",
    "Project & Workspace Management",
    "Schema Tools",
    "Diagnostics",
    "Other Tools",
]

TOOL_CATEGORIES: dict[str, str] = {
    # Note Management
    "write_note": "Note Management",
    "edit_note": "Note Management",
    "delete_note": "Note Management",
    "move_note": "Note Management",
    # Reading & Navigation
    "read_note": "Reading & Navigation",
    "view_note": "Reading & Navigation",
    "read_content": "Reading & Navigation",
    "build_context": "Reading & Navigation",
    "list_directory": "Reading & Navigation",
    "recent_activity": "Reading & Navigation",
    # Search
    "search_notes": "Search",
    "search": "Search",
    "fetch": "Search",
    # Project & Workspace Management
    "list_memory_projects": "Project & Workspace Management",
    "create_memory_project": "Project & Workspace Management",
    "delete_project": "Project & Workspace Management",
    "list_workspaces": "Project & Workspace Management",
    # Schema Tools
    "schema_validate": "Schema Tools",
    "schema_infer": "Schema Tools",
    "schema_diff": "Schema Tools",
    # Diagnostics
    "basic_memory_diagnostics": "Diagnostics",
}

# Parameters that are transport/plumbing details, not user-facing arguments.
SKIP_PARAMS = {"context", "self", "cls"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    type: str | None
    default: str | None
    required: bool
    description: str = ""


@dataclass
class Tool:
    name: str
    summary: str
    description: str
    params: list[Param] = field(default_factory=list)
    source_file: str = ""

    @property
    def category(self) -> str:
        return TOOL_CATEGORIES.get(self.name, "Other Tools")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _dotted_name(node: ast.AST | None) -> str | None:
    """Return a dotted name for a Name/Attribute node, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _unwrap_annotation(node: ast.expr | None) -> str | None:
    """Render a type annotation, unwrapping ``Annotated[T, ...]`` to just ``T``."""
    if node is None:
        return None
    if isinstance(node, ast.Subscript) and _dotted_name(node.value) in (
        "Annotated",
        "typing.Annotated",
    ):
        target = node.slice
        if isinstance(target, ast.Tuple) and target.elts:
            return ast.unparse(target.elts[0])
    return ast.unparse(node)


def _is_context_param(name: str, annotation: str | None) -> bool:
    """FastMCP ``Context`` is injected by the framework, not a user argument."""
    if name in SKIP_PARAMS:
        return True
    return bool(annotation) and "Context" in annotation


def _extract_params(func: ast.AsyncFunctionDef | ast.FunctionDef) -> list[Param]:
    """Extract user-facing parameters (positional + keyword-only) in order."""
    args = func.args
    params: list[Param] = []

    positional = list(args.posonlyargs) + list(args.args)
    # Align defaults to the tail of the positional list.
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults += list(args.defaults)

    for arg, default in zip(positional, defaults):
        annotation = _unwrap_annotation(arg.annotation)
        if _is_context_param(arg.arg, annotation):
            continue
        params.append(
            Param(
                name=arg.arg,
                type=annotation,
                default=ast.unparse(default) if default is not None else None,
                required=default is None,
            )
        )

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        annotation = _unwrap_annotation(arg.annotation)
        if _is_context_param(arg.arg, annotation):
            continue
        params.append(
            Param(
                name=arg.arg,
                type=annotation,
                default=ast.unparse(default) if default is not None else None,
                required=default is None,
            )
        )

    return params


_SECTION_RE = re.compile(
    r"^\s*(Args|Arguments|Parameters|Returns|Raises|Examples|Example|Note|Notes|Yields):\s*$"
)


def _split_docstring(docstring: str) -> tuple[str, str, dict[str, str]]:
    """Split a Google-style docstring into (summary, description, arg_descriptions).

    * ``summary`` — the first line/paragraph.
    * ``description`` — everything before the first section header (``Args:`` etc.).
    * ``arg_descriptions`` — mapping of parameter name to its description.
    """
    lines = docstring.splitlines()

    # Find the first section header.
    section_idx = len(lines)
    for i, line in enumerate(lines):
        if _SECTION_RE.match(line):
            section_idx = i
            break

    body = "\n".join(lines[:section_idx]).strip()
    parts = body.split("\n\n", 1)
    summary = parts[0].strip().replace("\n", " ")
    description = parts[1].strip() if len(parts) > 1 else ""

    arg_descriptions = _parse_args_section(lines, section_idx)
    return summary, description, arg_descriptions


def _parse_args_section(lines: list[str], start: int) -> dict[str, str]:
    """Parse a Google-style ``Args:`` block into {name: description}."""
    result: dict[str, str] = {}
    # Locate the Args-style header.
    idx = None
    for i in range(start, len(lines)):
        header = _SECTION_RE.match(lines[i])
        if header and header.group(1) in ("Args", "Arguments", "Parameters"):
            idx = i + 1
            break
    if idx is None:
        return result

    param_re = re.compile(r"^(\s+)(\*{0,2}\w+)\s*(?:\([^)]*\))?:\s*(.*)$")
    current: str | None = None
    base_indent: int | None = None

    for line in lines[idx:]:
        if _SECTION_RE.match(line):
            break
        if not line.strip():
            continue
        m = param_re.match(line)
        indent = len(line) - len(line.lstrip())
        if m and (base_indent is None or indent <= base_indent):
            base_indent = indent
            name = m.group(2).lstrip("*")
            result[name] = m.group(3).strip()
            current = name
        elif current is not None:
            # Continuation line for the current parameter.
            result[current] = f"{result[current]} {line.strip()}".strip()
    return result


def _tool_metadata(func: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """Return the ``description`` kwarg from the ``@mcp.tool(...)`` decorator."""
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if _dotted_name(dec.func) not in ("mcp.tool", "tool"):
            continue
        for kw in dec.keywords:
            if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value).strip()
    return None


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _load_registered_tool_names() -> list[str]:
    """Parse ``__all__`` from the tools package ``__init__.py``."""
    init_path = TOOLS_DIR / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                return [
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    raise RuntimeError("Could not find __all__ in tools/__init__.py")


def _collect_tools() -> list[Tool]:
    """Find every registered tool's function definition across the tool files."""
    registered = set(_load_registered_tool_names())
    tools: dict[str, Tool] = {}

    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name not in registered:
                continue
            docstring = ast.get_docstring(node) or ""
            summary, description, arg_descriptions = _split_docstring(docstring)
            decorator_desc = _tool_metadata(node)
            if not summary and decorator_desc:
                summary = decorator_desc

            params = _extract_params(node)
            for param in params:
                param.description = arg_descriptions.get(param.name, "")

            tools[node.name] = Tool(
                name=node.name,
                summary=summary or decorator_desc or "",
                description=description,
                params=params,
                source_file=path.name,
            )

    missing = registered - tools.keys()
    if missing:
        raise RuntimeError(f"Registered tools not found in source: {sorted(missing)}")

    return list(tools.values())


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _anchor(text: str) -> str:
    """Approximate a GitHub Markdown heading anchor.

    GitHub lowercases, drops characters that are not word chars/space/hyphen,
    then replaces each remaining whitespace character with a hyphen *without*
    collapsing runs. So ``Reading & Navigation`` -> ``reading--navigation``.
    """
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text.strip())


class AnchorRegistry:
    """Hand out heading anchors in document order, the way GitHub does.

    GitHub keeps the first heading with a given slug bare and suffixes later
    ones with ``-1``, ``-2``, ... A ``Search`` category followed by a ``search``
    tool would otherwise both link to ``#search`` and the tool's TOC entry would
    land on the category. Every heading in the document must be registered, in
    order, for the suffixes to line up with what GitHub renders.
    """

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}

    def register(self, heading_text: str) -> str:
        base = _anchor(heading_text)
        anchor = base
        while anchor in self._occurrences:
            self._occurrences[base] += 1
            anchor = f"{base}-{self._occurrences[base]}"
        self._occurrences[anchor] = 0
        return anchor


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_MARK = "```"
TOOL_HEADING_LEVEL = 3
MAX_HEADING_LEVEL = 6


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return ``(line_index, level, text)`` for each heading outside fenced code."""
    found: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(_FENCE_MARK):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            found.append((index, len(match.group(1)), match.group(2)))
    return found


def _nest_headings(text: str, parent_level: int) -> str:
    """Shift a docstring's own headings so they sit below the tool heading.

    Docstrings are free to structure themselves with ``##``/``###`` (search_notes
    does). Emitted verbatim under a ``###`` tool heading they close the tool's
    section, so its parameter table ends up under the docstring's last heading and
    the docstring headings read as sibling tools in any rendered outline. Shift
    them so the shallowest embedded heading lands one level below the parent.
    """
    lines = text.splitlines()
    headings = _headings(lines)
    if not headings:
        return text
    shift = max(0, parent_level + 1 - min(level for _, level, _ in headings))
    if shift == 0:
        return text
    for index, level, heading_text in headings:
        lines[index] = f"{'#' * min(MAX_HEADING_LEVEL, level + shift)} {heading_text}"
    return "\n".join(lines)


def _escape_cell(text: str | None) -> str:
    """Escape a value for use inside a Markdown table cell."""
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _render_params_table(params: list[Param]) -> str:
    if not params:
        return "_No parameters._\n"
    lines = [
        "| Parameter | Type | Required | Default | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in params:
        type_cell = f"`{_escape_cell(p.type)}`" if p.type else ""
        required = "Yes" if p.required else "No"
        default_cell = f"`{_escape_cell(p.default)}`" if p.default is not None else ""
        lines.append(
            f"| `{p.name}` | {type_cell} | {required} | {default_cell} "
            f"| {_escape_cell(p.description)} |"
        )
    return "\n".join(lines) + "\n"


def _render(tools: list[Tool]) -> str:
    by_category: dict[str, list[Tool]] = {cat: [] for cat in CATEGORY_ORDER}
    for tool in sorted(tools, key=lambda t: t.name):
        by_category.setdefault(tool.category, []).append(tool)

    # Anchors are assigned in document order, and the table of contents comes before
    # the sections it links to, so render the sections first and build the TOC from
    # the anchors they were actually given.
    title = "Basic Memory MCP Tool Reference"
    anchors = AnchorRegistry()
    anchors.register(title)
    anchors.register("Table of Contents")
    category_anchors: dict[str, str] = {}
    tool_anchors: dict[str, str] = {}

    sections: list[str] = []
    for category in CATEGORY_ORDER:
        entries = by_category.get(category)
        if not entries:
            continue
        category_anchors[category] = anchors.register(category)
        sections.append(f"## {category}\n")
        for tool in entries:
            tool_anchors[tool.name] = anchors.register(tool.name)
            sections.append(f"### `{tool.name}`\n")
            if tool.summary:
                sections.append(f"{tool.summary}\n")
            detail = _nest_headings(tool.description.strip(), TOOL_HEADING_LEVEL)
            if detail:
                for _, _, heading_text in _headings(detail.splitlines()):
                    anchors.register(heading_text)
                sections.append(f"{detail}\n")
            sections.append("**Parameters:**\n")
            sections.append(_render_params_table(tool.params))
            sections.append(f"_Source: `src/basic_memory/mcp/tools/{tool.source_file}`_\n")

    out: list[str] = []
    out.append(f"# {title}\n")
    out.append(
        "> **Auto-generated** by `scripts/generate_tool_docs.py`. Do not edit by hand.\n"
        ">\n"
        "> Regenerate with: `just tool-docs` (or `uv run python scripts/generate_tool_docs.py`)\n"
    )
    out.append(
        f"This reference documents all **{len(tools)}** MCP tools registered by "
        "Basic Memory. Each entry lists the tool's purpose and its parameters "
        "(types, whether they are required, defaults, and descriptions).\n"
    )

    out.append("## Table of Contents\n")
    for category in CATEGORY_ORDER:
        entries = by_category.get(category)
        if not entries:
            continue
        out.append(f"- [{category}](#{category_anchors[category]})")
        for tool in entries:
            out.append(f"  - [`{tool.name}`](#{tool_anchors[tool.name]})")
    out.append("")

    out.extend(sections)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    tools = _collect_tools()
    markdown = _render(tools)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(tools)} tools).")


if __name__ == "__main__":
    main()
