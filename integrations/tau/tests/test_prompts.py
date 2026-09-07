"""Exercise the templates through Tau's actual resource discovery and expansion."""

import shutil
from pathlib import Path

from tau_coding.prompt_templates import (
    load_prompt_templates_with_diagnostics,
    substitute_prompt_template_args,
)
from tau_coding.resources import TauResourcePaths


def test_bundled_prompts_discover_and_expand(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "prompts", tmp_path / "tau/prompts")
    templates, diagnostics = load_prompt_templates_with_diagnostics(
        TauResourcePaths(root=tmp_path / "tau", agents_root=tmp_path / "agents", cwd=tmp_path)
    )
    assert not diagnostics
    assert {template.name for template in templates} == {
        "bm-resume",
        "bm-plan",
        "bm-decide",
        "bm-wrap-up",
    }
    for template in templates:
        assert template.description
        expanded = substitute_prompt_template_args(template.content, ["shared session identity"])
        assert "shared session identity" in expanded
        assert "$ARGUMENTS" not in expanded
        assert "$ARGUMENTS" not in substitute_prompt_template_args(template.content, [])
