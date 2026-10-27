"""Shipped guides and Pi's direct CLI transport must use supported tool verbs."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIPPED_REFERENCES = (
    "README.md",
    "docs/COMPACT_DISCOVERY.md",
    "docs/metadata-search.md",
    "docs/manual-pages.md",
    "docs/1.0.md",
    "benchmarks/src/basic_memory_benchmarks/providers/bm_cloud.py",
    "src/basic_memory/man/bm.1",
    "skills/memory-literary-analysis/SKILL.md",
    "plugins/claude-code/skills/bm-setup/SKILL.md",
    "integrations/pi/README.md",
    "integrations/pi/extensions/index.ts",
)
RETIRED_CALL = re.compile(
    r"\btool(?:\s+|[\"']\s*,\s*[\"'])"
    r"(write-note|read-note|edit-note|delete-note|search-notes|build-context|"
    r"recent-activity|list-projects|list-workspaces|schema-validate|schema-infer|schema-diff)\b"
)


def test_shipped_consumers_do_not_invoke_retired_tool_commands() -> None:
    failures = [
        f"{relative}: {match.group(1)}"
        for relative in SHIPPED_REFERENCES
        for match in RETIRED_CALL.finditer((ROOT / relative).read_text(encoding="utf-8"))
    ]
    assert failures == []


def test_benchmark_mcp_calls_use_registered_surface() -> None:
    # Audit executable calls, including multiline calls, rather than historical prose.
    supported = {
        "bm_read",
        "bm_write",
        "bm_edit",
        "bm_delete",
        "bm_move",
        "bm_search",
        "bm_context",
        "bm_list",
        "bm_find",
        "bm_project",
        "bm_project_delete",
        "bm_schema",
        "bm_man",
    }
    failures: list[str] = []
    for directory in (ROOT / "benchmarks/scripts", ROOT / "benchmarks/src"):
        for source in directory.rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "call_tool"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value not in supported
                ):
                    failures.append(
                        f"{source.relative_to(ROOT)}:{node.lineno}: {node.args[0].value}"
                    )
    assert failures == []
