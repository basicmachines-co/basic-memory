#!/usr/bin/env python3
"""Generate MCP tool reference documentation from tool docstrings.

Introspects all MCP tool definitions in src/basic_memory/mcp/tools/,
extracts names, descriptions, parameters, and docstrings, then emits
a single deterministic markdown reference to docs/mcp-tools.md.

Usage:
    uv run scripts/generate_tool_docs.py

    # Verify idempotency (diff should be empty):
    uv run scripts/generate_tool_docs.py && uv run scripts/generate_tool_docs.py
    git diff docs/mcp-tools.md
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "src" / "basic_memory" / "mcp" / "tools"
OUT_FILE = ROOT / "docs" / "mcp-tools.md"

# Only files that contain public MCP tools (skip helpers/internals).
# NOTE: ui_sdk.py is intentionally omitted — its two tools are commented out
# in src/basic_memory/mcp/__init__.py and therefore not public.
TOOL_FILES = [
    "build_context.py",
    "canvas.py",
    "chatgpt_tools.py",
    "cloud_info.py",
    "delete_note.py",
    "edit_note.py",
    "list_directory.py",
    "move_note.py",
    "project_management.py",
    "read_content.py",
    "read_note.py",
    "recent_activity.py",
    "release_notes.py",
    "schema.py",
    "search.py",
    "view_note.py",
    "workspaces.py",
    "write_note.py",
]


# ---------------------------------------------------------------------------
# AST-based extraction (no import side-effects)
# ---------------------------------------------------------------------------


def _clean_docstring(raw: str | None) -> str:
    """Dedent and strip a raw docstring."""
    if not raw:
        return ""
    return inspect.cleandoc(raw)


def _get_default_repr(node: ast.expr | None) -> str:
    """Return a short string representation for a default-value AST node."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _unwrap_annotated(text: str) -> str:
    """Bracket-aware unwrap of ``Annotated[X, ...]`` → ``X``.

    The naïve regex ``Annotated\\[([^,\\]]+),.*?\\]`` breaks on types whose
    first argument itself contains brackets or commas, e.g.
    ``Annotated[Dict[str, Any] | None, ...]`` or
    ``Annotated[List[str] | None, BeforeValidator(...), ...]``.
    This function scans character-by-character to find the first
    *top-level* comma that separates the type from the metadata args,
    then discards everything from that comma to the matching closing ``]``.
    """
    prefix = "Annotated["
    result = []
    i = 0
    while i < len(text):
        if text[i:].startswith(prefix):
            # Walk past "Annotated[" and collect the first top-level argument.
            j = i + len(prefix)
            depth = 0
            start = j
            while j < len(text):
                ch = text[j]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    if depth == 0:
                        # Closing bracket of Annotated[...] with no comma found
                        # (shouldn't happen in valid annotations, but be safe).
                        break
                    depth -= 1
                elif ch == "," and depth == 0:
                    # Found the separator between the type arg and metadata.
                    break
                j += 1
            inner_type = text[start:j]
            result.append(inner_type)
            # Skip past the rest of the Annotated[...] construct.
            # We need to find the matching ']' for the original 'Annotated['.
            depth = 0
            while j < len(text):
                ch = text[j]
                if ch in "([{":
                    depth += 1
                elif ch == "]" and depth == 0:
                    j += 1  # consume the closing ']'
                    break
                elif ch in ")]}":
                    depth -= 1
                j += 1
            i = j
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def _annotation_repr(node: ast.expr | None) -> str:
    """Return a readable string for a type-annotation AST node."""
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except Exception:
        return ""
    # Unwrap Annotated[X, ...] → X  (keeps output readable).
    # Use a bracket-aware unwrap so types like Dict[str, Any] | None are
    # preserved correctly instead of being truncated at the first comma.
    text = _unwrap_annotated(text)
    return text


_SENTINEL = object()  # returned when decorator IS @mcp.tool but has no description string


def _mcp_tool_description(decorator: ast.expr) -> str | None | object:
    """Extract the ``description=`` keyword from an ``@mcp.tool(...)`` decorator call.

    Returns:
        - A string when the decorator carries ``description="..."``
        - ``_SENTINEL`` when the decorator IS an mcp.tool call but has no description
        - ``None`` when the decorator is not an mcp.tool call at all

    This distinction lets callers treat mcp.tool-decorated functions without a
    decorator description string as still valid tools (description comes from the
    docstring in that case).
    """
    if not isinstance(decorator, ast.Call):
        return None

    func = decorator.func
    # Accept both ``mcp.tool(...)`` and ``tool(...)``
    if not (
        (isinstance(func, ast.Attribute) and func.attr == "tool")
        or (isinstance(func, ast.Name) and func.id == "tool")
    ):
        return None

    for kw in decorator.keywords:
        if kw.arg == "description" and isinstance(kw.value, ast.Constant):
            return kw.value.value

    # Also accept a positional string as the tool name (no description)
    # e.g. @mcp.tool("list_memory_projects", annotations={...})
    # In this case, the description is not in the decorator; fall through to docstring.
    return _SENTINEL


class ToolInfo:
    """Holds extracted metadata for a single MCP tool."""

    __slots__ = (
        "name",
        "decorator_description",
        "docstring",
        "params",
        "source_file",
    )

    def __init__(
        self,
        name: str,
        decorator_description: str,
        docstring: str,
        params: list[dict[str, str]],
        source_file: str,
    ) -> None:
        self.name = name
        self.decorator_description = decorator_description
        self.docstring = docstring
        self.params = params
        self.source_file = source_file


# Parameters that are MCP/framework plumbing — not useful to document.
_SKIP_PARAMS = frozenset({"context", "self", "cls"})


def extract_tools_from_file(path: Path) -> list[ToolInfo]:
    """Parse *path* with AST and return a list of ToolInfo for every @mcp.tool function."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"WARNING: could not parse {path}: {exc}", file=sys.stderr)
        return []

    tools: list[ToolInfo] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Only process functions that have an @mcp.tool (or @tool) decorator.
        # decorator_desc is:
        #   - a non-empty string when found in the decorator
        #   - _SENTINEL when the decorator is mcp.tool but carries no description
        #   - None when no mcp.tool decorator is present
        decorator_desc: str | object | None = None
        for dec in node.decorator_list:
            result = _mcp_tool_description(dec)
            if result is not None:
                decorator_desc = result
                break

        if decorator_desc is None:
            continue

        # When the decorator doesn't carry a description string, fall back to
        # the first sentence of the docstring (populated below).
        decorator_has_description = isinstance(decorator_desc, str)

        docstring = _clean_docstring(ast.get_docstring(node))

        # --- Parameters ---
        params: list[dict[str, str]] = []
        args = node.args
        # Defaults are right-aligned against the arg list
        n_defaults = len(args.defaults)
        n_args = len(args.args)
        defaults_padded = [None] * (n_args - n_defaults) + list(args.defaults)  # type: ignore[list-item]
        kw_defaults = args.kw_defaults  # may contain None for "no default"

        for i, arg in enumerate(args.args):
            if arg.arg in _SKIP_PARAMS:
                continue
            default = defaults_padded[i]
            params.append(
                {
                    "name": arg.arg,
                    "type": _annotation_repr(arg.annotation),
                    "default": _get_default_repr(default) if default is not None else "",
                }
            )

        for i, arg in enumerate(args.kwonlyargs):
            if arg.arg in _SKIP_PARAMS:
                continue
            default = kw_defaults[i] if i < len(kw_defaults) else None
            params.append(
                {
                    "name": arg.arg,
                    "type": _annotation_repr(arg.annotation),
                    "default": _get_default_repr(default) if default is not None else "",
                }
            )

        # Build the short one-liner description:
        # - prefer the explicit decorator description=
        # - fall back to the first non-blank line of the docstring
        if decorator_has_description:
            short_desc = decorator_desc.strip()  # type: ignore[union-attr]
        else:
            first_line = next((ln.strip() for ln in docstring.splitlines() if ln.strip()), "")
            short_desc = first_line

        tools.append(
            ToolInfo(
                name=node.name,
                decorator_description=short_desc,
                docstring=docstring,
                params=params,
                source_file=path.name,
            )
        )

    return tools


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _escape_table_cell(text: str) -> str:
    """Escape ``|`` characters so they do not break GitHub-flavoured markdown tables."""
    return text.replace("|", "\\|")


def _params_table(
    params: list[dict[str, str]], arg_descriptions: dict[str, str] | None = None
) -> str:
    """Render parameters as a markdown table.

    ``arg_descriptions`` maps parameter name → one-line description extracted
    from the docstring ``Args:`` block.
    """
    if not params:
        return ""
    arg_descriptions = arg_descriptions or {}
    rows = [
        "| Parameter | Type | Default | Description |",
        "|-----------|------|---------|-------------|",
    ]
    for p in params:
        name = f"`{p['name']}`"
        # Pipe characters inside a table cell break GitHub's renderer; escape them.
        raw_type = p["type"]
        typ = f"`{_escape_table_cell(raw_type)}`" if raw_type else ""
        default_val = p["default"]
        default = f"`{_escape_table_cell(default_val)}`" if default_val else "*(required)*"
        desc = _escape_table_cell(arg_descriptions.get(p["name"], ""))
        rows.append(f"| {name} | {typ} | {default} | {desc} |")
    return "\n".join(rows)


def _extract_examples_section(docstring: str) -> tuple[str, str]:
    """Split docstring into (body_without_examples, examples_block).

    Looks for an 'Examples:' section (with or without leading '#') and
    returns everything before it as the body, and everything from the
    Examples header to the next top-level section (e.g. 'Raises:',
    'Returns:') as examples.  Trailing sections after Examples are
    re-appended to the body so they are not silently dropped.
    """
    # Match headings like "Examples:", "# Examples", "## Examples", etc.
    example_pattern = re.compile(r"^(#{1,3}\s*)?Examples:?\s*$", re.MULTILINE | re.IGNORECASE)
    match = example_pattern.search(docstring)
    if not match:
        return docstring.strip(), ""

    body_before = docstring[: match.start()].strip()
    after_header = docstring[match.end() :]

    # Find the next top-level section that is NOT indented (Raises:, Returns:, etc.)
    # so we do not swallow it into the examples block.
    next_section = re.search(r"\n(?=[A-Z][^\n]*:$)", after_header, re.MULTILINE)
    if next_section:
        examples_raw = after_header[: next_section.start()].strip()
        trailing = after_header[next_section.start() :].strip()
        # Re-attach the trailing section to the body.
        body = (body_before + "\n\n" + trailing).strip() if trailing else body_before
    else:
        examples_raw = after_header.strip()
        body = body_before

    return body, examples_raw


def _parse_args_block(args_text: str) -> dict[str, str]:
    """Parse a docstring ``Args:`` block into a ``{param_name: description}`` dict.

    Handles the standard Google-style format with optional leading indentation::

        param_name: First line of description.
            Continuation lines are more deeply indented.

    Works with both un-indented and uniformly-indented blocks (e.g. when the
    raw docstring is already cleandoc'd but the Args entries still have a
    consistent leading indent from the original source indentation).

    Returns a dict mapping each parameter name to its single-line summary
    (first line only, stripped) for use in the parameters table.
    """
    result: dict[str, str] = {}

    # Determine the base indent level: the minimum indent of non-empty lines.
    # Parameter entries sit at this level; continuation lines are deeper.
    non_empty = [ln for ln in args_text.splitlines() if ln.strip()]
    if not non_empty:
        return result
    base_indent = min(len(ln) - len(ln.lstrip()) for ln in non_empty)

    current_name: str | None = None
    current_lines: list[str] = []

    for line in args_text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        # A new parameter entry sits at the base indent level with "name: ..."
        if indent == base_indent:
            top_level = re.match(r"(\w+)\s*:(.*)", stripped)
            if top_level:
                if current_name is not None:
                    result[current_name] = " ".join(current_lines).strip()
                current_name = top_level.group(1)
                rest = top_level.group(2).strip()
                current_lines = [rest] if rest else []
                continue
        # Continuation line — only capture the first continuation line to
        # keep descriptions terse enough for a table cell.
        if current_name is not None and not current_lines:
            current_lines.append(stripped)

    if current_name is not None:
        result[current_name] = " ".join(current_lines).strip()

    return result


def _format_args_section(docstring: str) -> tuple[str, dict[str, str]]:
    """Remove the 'Args:' block from a docstring and return (cleaned_body, arg_descriptions).

    ``arg_descriptions`` maps each parameter name to its one-line description
    so callers can populate the Description column of the params table.
    """
    pattern = re.compile(r"^Args:\s*$", re.MULTILINE)
    match = pattern.search(docstring)
    if not match:
        return docstring.strip(), {}

    before = docstring[: match.start()].strip()
    after_start = match.end()

    # The Args block ends at the next top-level section (line that starts
    # without indentation and ends with ':'), or at end-of-string.
    next_section = re.search(r"\n(?=[A-Z][^\n:]*:$)", docstring[after_start:], re.MULTILINE)
    if next_section:
        # Use lstrip("\n") not strip() so that leading spaces (indent) on the
        # first arg line are preserved for _parse_args_block's indent detection.
        args_text = docstring[after_start : after_start + next_section.start()].lstrip("\n")
        remainder = docstring[after_start + next_section.start() :].strip()
        body = (before + "\n\n" + remainder).strip()
    else:
        args_text = docstring[after_start:].lstrip("\n")
        body = before.strip()

    return body, _parse_args_block(args_text)


def render_tool_section(tool: ToolInfo, anchor_suffix: str = "") -> str:
    """Return a markdown section for a single tool.

    ``anchor_suffix`` is appended to the HTML id anchor injected before the
    heading when the tool name would collide with a group-section anchor
    (e.g. the ``search`` tool inside the ``## Search`` group).
    """
    lines: list[str] = []
    if anchor_suffix:
        # Inject an explicit anchor so the TOC link resolves to the tool
        # section rather than the same-named group header.
        lines.append(f'<a id="{tool.name.replace("_", "-")}{anchor_suffix}"></a>')
        lines.append("")
    lines.append(f"### `{tool.name}`")
    lines.append("")

    # One-liner description from the decorator (most concise)
    lines.append(tool.decorator_description)
    lines.append("")

    # Full docstring body (strip Args: and Examples: sub-sections which get
    # their own formatting below)
    doc = tool.docstring
    doc, arg_descriptions = _format_args_section(doc)
    doc, examples_text = _extract_examples_section(doc)

    # Avoid duplicating the decorator description: if the docstring's first
    # non-blank line is essentially the same sentence, drop it.
    if doc:
        doc_lines = doc.splitlines()
        first_nonempty_idx = next(
            (i for i, ln in enumerate(doc_lines) if ln.strip()),
            None,
        )
        if first_nonempty_idx is not None:
            first_line = doc_lines[first_nonempty_idx].strip()
            # Normalise both sides for comparison (lowercase, strip punctuation)
            norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
            if norm(first_line) == norm(tool.decorator_description):
                doc_lines.pop(first_nonempty_idx)
                doc = "\n".join(doc_lines).strip()

    # Demote any `###` sub-headers in the body to `####` so they do not
    # collide with tool-name headers in GitHub's outline sidebar.
    doc = re.sub(r"^### ", "#### ", doc, flags=re.MULTILINE)

    if doc:
        lines.append(doc)
        lines.append("")

    # Parameters table
    if tool.params:
        lines.append("**Parameters**")
        lines.append("")
        lines.append(_params_table(tool.params, arg_descriptions))
        lines.append("")

    # Examples block
    if examples_text:
        lines.append("**Examples**")
        lines.append("")
        # Wrap in a python code fence if not already fenced
        if "```" not in examples_text:
            lines.append("```python")
            lines.append(examples_text)
            lines.append("```")
        else:
            lines.append(examples_text)
        lines.append("")

    lines.append(f"*Source: `src/basic_memory/mcp/tools/{tool.source_file}`*")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

# Stable, hand-curated grouping of tools into logical categories.
# Tools not listed here fall into "Other Tools".
TOOL_GROUPS: dict[str, list[str]] = {
    "Note Management": [
        "write_note",
        "read_note",
        "view_note",
        "edit_note",
        "move_note",
        "delete_note",
    ],
    "Reading & Navigation": [
        "read_content",
        "build_context",
        "recent_activity",
        "list_directory",
    ],
    "Search": [
        "search_notes",
        "search",
        "fetch",
    ],
    "Project & Workspace Management": [
        "list_memory_projects",
        "create_memory_project",
        "delete_project",
        "list_workspaces",
    ],
    "Schema Tools": [
        "schema_validate",
        "schema_infer",
        "schema_diff",
    ],
    "Visualization": [
        "canvas",
    ],
    "Info & Utilities": [
        "cloud_info",
        "release_notes",
    ],
}


def group_tools(tools: list[ToolInfo]) -> dict[str, list[ToolInfo]]:
    """Return an ordered dict mapping group name → list of ToolInfo."""
    by_name: dict[str, ToolInfo] = {t.name: t for t in tools}
    result: dict[str, list[ToolInfo]] = {}

    placed: set[str] = set()
    for group, names in TOOL_GROUPS.items():
        members = [by_name[n] for n in names if n in by_name]
        if members:
            result[group] = members
            placed.update(n for n in names if n in by_name)

    # Anything not in TOOL_GROUPS goes to "Other Tools", sorted alphabetically
    other = sorted([t for t in tools if t.name not in placed], key=lambda t: t.name)
    if other:
        result["Other Tools"] = other

    return result


# ---------------------------------------------------------------------------
# Top-level document builder
# ---------------------------------------------------------------------------

HEADER = """\
<!--
  This file is AUTO-GENERATED. Do not edit it by hand.

  To regenerate:
      uv run scripts/generate_tool_docs.py

  Source: scripts/generate_tool_docs.py
-->

# Basic Memory MCP Tool Reference

Complete reference for all MCP tools exposed by the Basic Memory server.
Tools are grouped by function. Parameters marked *(required)* have no default value.

> **Regenerating this file**: run `uv run scripts/generate_tool_docs.py` from the
> repository root. The output is deterministic; running it twice should produce
> an identical file (zero diff).

## Table of Contents

"""


def _tool_anchor(tool_name: str, group_anchors: set[str]) -> str:
    """Return a stable GitHub anchor for a tool heading.

    If the plain ``tool_name`` (with ``_`` → ``-``) would collide with a
    group-section anchor (e.g. the ``search`` tool inside the ``## Search``
    group), append ``-tool`` to disambiguate.
    """
    base = tool_name.replace("_", "-")
    if base in group_anchors:
        return base + "-tool"
    return base


def build_toc(groups: dict[str, list[ToolInfo]]) -> str:
    lines: list[str] = []

    # Collect all group anchors first so tool anchors can avoid collisions.
    group_anchors: set[str] = set()
    for group in groups:
        anchor = re.sub(r"[^\w\s-]", "", group.lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        group_anchors.add(anchor)

    for group, members in groups.items():
        # GitHub-style anchor: lowercase, spaces → hyphens, drop punctuation
        anchor = re.sub(r"[^\w\s-]", "", group.lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        lines.append(f"- [{group}](#{anchor})")
        for tool in members:
            t_anchor = _tool_anchor(tool.name, group_anchors)
            lines.append(f"  - [`{tool.name}`](#{t_anchor})")
    return "\n".join(lines)


def build_document(groups: dict[str, list[ToolInfo]]) -> str:
    # Pre-compute group anchors so tool-section rendering can inject explicit
    # HTML id attributes where tool names would collide with group headers.
    group_anchors: set[str] = set()
    for group in groups:
        anchor = re.sub(r"[^\w\s-]", "", group.lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        group_anchors.add(anchor)

    parts: list[str] = [HEADER]
    parts.append(build_toc(groups))
    parts.append("\n\n---\n")

    for group, members in groups.items():
        parts.append(f"\n## {group}\n")
        for tool in members:
            base = tool.name.replace("_", "-")
            suffix = "-tool" if base in group_anchors else ""
            parts.append(render_tool_section(tool, anchor_suffix=suffix))
            parts.append("---\n")

    # Trailing newline
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    all_tools: list[ToolInfo] = []
    for filename in TOOL_FILES:
        path = TOOLS_DIR / filename
        if not path.exists():
            print(f"WARNING: {path} does not exist — skipping", file=sys.stderr)
            continue
        file_tools = extract_tools_from_file(path)
        all_tools.extend(file_tools)

    if not all_tools:
        print("ERROR: no tools found — check TOOLS_DIR path", file=sys.stderr)
        return 1

    # Stable sort: group order is defined by TOOL_GROUPS; within groups, order
    # is defined by TOOL_GROUPS list. Global sort here is just for determinism
    # of "Other Tools".
    groups = group_tools(all_tools)
    document = build_document(groups)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(document, encoding="utf-8")

    total = sum(len(m) for m in groups.values())
    print(
        f"Generated {OUT_FILE.relative_to(ROOT)} ({total} tools, {len(OUT_FILE.read_text().splitlines())} lines)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
