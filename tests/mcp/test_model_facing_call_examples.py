"""Tool-call examples in model-facing text must be callable as written (#1633).

Agents copy these snippets verbatim. A positional example in the wrong order
(`read_note("qa", "notes/x")` reads a note named "qa" from project "notes/x")
or a call to a tool that no longer exists sends them the wrong way. The #1603
audit fixed a round of these by hand; this test keeps them fixed.

The rule: an example names a registered tool, passes at most its first
argument positionally, and passes every other argument by keyword under a
parameter that tool accepts.
"""

import ast
import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

import basic_memory.mcp.tools as tools_module

MCP_ROOT = Path(tools_module.__file__).resolve().parent.parent

# Model-facing text lives in tool modules (descriptions, docstrings, guidance
# returned on errors), prompts, and resources. Typed clients and routing code
# document Python APIs for developers and are out of scope.
MODEL_FACING_DIRS = ("tools", "prompts", "resources")

TOOL_FUNCTIONS = {name: getattr(tools_module, name) for name in tools_module.__all__}

# Tools that were renamed or retired. An example calling one is always stale.
RETIRED_TOOL_NAMES = frozenset(
    {
        "list_projects",
        "switch_project",
        "get_current_project",
        "set_default_project",
        "sync_status",
        "project_info",
    }
)

CALL_START = re.compile(r"(?<![\w.])([a-z_][a-z0-9_]*)\(")


def _rendered_strings(source: str) -> Iterator[tuple[int, str]]:
    """Yield every string literal in a module, with f-string holes rendered as X.

    A hole directly followed by an identifier is a prefix fragment such as
    `{project_arg}title="..."` (where project_arg renders `project="x", `), so it
    renders as nothing rather than gluing X onto the next keyword.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for index, value in enumerate(node.values):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    continue
                following = node.values[index + 1] if index + 1 < len(node.values) else None
                prefix_fragment = (
                    isinstance(following, ast.Constant)
                    and isinstance(following.value, str)
                    and re.match(r"[a-z_]", following.value) is not None
                )
                parts.append("" if prefix_fragment else "X")
            yield node.lineno, "".join(parts)


def _balanced_call(text: str, open_index: int) -> str | None:
    """Return the text from `name(` through its matching `)`, or None if unbalanced."""
    depth = 0
    quote: str | None = None
    for index in range(open_index, len(text)):
        char = text[index]
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[: index + 1]
    return None


def _call_examples() -> Iterator[tuple[str, str, str]]:
    """Yield (location, tool name, call snippet) for each example in model-facing text."""
    known = TOOL_FUNCTIONS.keys() | RETIRED_TOOL_NAMES
    for directory in MODEL_FACING_DIRS:
        for path in sorted((MCP_ROOT / directory).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for lineno, text in _rendered_strings(source):
                for match in CALL_START.finditer(text):
                    name = match.group(1)
                    if name not in known:
                        continue
                    snippet = _balanced_call(text[match.start() :], match.end() - match.start() - 1)
                    if snippet is None:
                        continue
                    location = f"{path.relative_to(MCP_ROOT)}:{lineno}"
                    yield location, name, snippet


def _violations(name: str, snippet: str) -> list[str]:
    if name in RETIRED_TOOL_NAMES:
        return [f"calls retired tool {name}()"]
    try:
        call = ast.parse(snippet.replace("...", "None"), mode="eval").body
    except SyntaxError:
        # Prose such as `search_notes(query, page)` in a sentence is not an example.
        return []
    if not isinstance(call, ast.Call):
        return []
    problems = []
    if len(call.args) > 1:
        problems.append(
            f"passes {len(call.args)} positional arguments; pass all but the first by keyword"
        )
    parameters = inspect.signature(TOOL_FUNCTIONS[name]).parameters
    for keyword in call.keywords:
        if keyword.arg is not None and keyword.arg not in parameters:
            problems.append(f"{keyword.arg}= is not a parameter of {name}")
    return problems


def test_scanner_finds_examples():
    """Guard the guard: the scan must actually see the tool modules' examples."""
    examples = list(_call_examples())
    assert len(examples) > 50
    assert {name for _, name, _ in examples} >= {"read_note", "search_notes", "edit_note"}


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('read_note("qa", "notes/x")', "positional"),
        ('read_note(identifier="x", folder="y")', "folder= is not a parameter"),
        ("list_projects()", "retired"),
    ],
)
def test_rule_rejects_known_bad_shapes(snippet, expected):
    name = snippet.split("(", 1)[0]
    assert any(expected in problem for problem in _violations(name, snippet))


def test_model_facing_call_examples_are_callable_as_written():
    failures = [
        f"{location}: {snippet} -> {problem}"
        for location, name, snippet in _call_examples()
        for problem in _violations(name, snippet)
    ]
    assert failures == [], "\n".join(failures)
