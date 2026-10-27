"""Shipped guides and Pi's direct CLI transport must use supported tool verbs."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIPPED_REFERENCES = (
    "README.md",
    "docs/COMPACT_DISCOVERY.md",
    "docs/metadata-search.md",
    "docs/manual-pages.md",
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
