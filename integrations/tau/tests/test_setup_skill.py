"""Exercise the documented copied-skill installation with Tau's real loader."""

import json
import re
from pathlib import Path
from shutil import copytree

from tau.bridge import Settings
from tau_coding.resources import TauResourcePaths
from tau_coding.skills import expand_skill_command, load_skills_with_diagnostics


def test_setup_skill_discovery_invocation_and_policy_examples(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "skills" / "basic-memory-setup"
    target = tmp_path / "skills" / source.name
    copytree(source, target)
    skills, diagnostics = load_skills_with_diagnostics(
        TauResourcePaths(root=tmp_path, agents_root=None, project_resources_enabled=False)
    )
    assert diagnostics == []
    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "basic-memory-setup"
    assert skill.description
    assert not skill.disable_model_invocation
    assert skill.path == target / "SKILL.md"
    expanded = expand_skill_command("/skill:basic-memory-setup", skills)
    assert expanded is not None
    assert "Choose destination and policy" in expanded

    examples = re.findall(r"```json\n(.*?)\n```", skill.content, re.DOTALL)
    assert len(examples) == 4
    tools, recall, continuity, coding = [
        Settings.model_validate_json(example) for example in examples
    ]
    assert coding.repositories[0].kind == "coding"
    assert coding.repositories[0].project == "CHOSEN_PROJECT"
    assert coding.project is None
    assert tools.project is None
    assert not tools.auto_recall
    assert recall.project == continuity.project == "CHOSEN_PROJECT"
    assert recall.auto_recall and continuity.auto_recall
    for settings in (tools, recall):
        assert not settings.capture_knowledge
        assert not settings.checkpoint_on_compact
        assert not settings.summarize_on_shutdown
    assert continuity.capture_knowledge
    assert continuity.checkpoint_on_compact
    assert continuity.summarize_on_shutdown
    assert all(not settings.capture_transcript for settings in (tools, recall, continuity))

    # Applying a policy must not replace unrelated customized settings.
    existing = Settings(command="/custom/bm", timeout_seconds=90, checkpoint_folder="handoffs")
    for example in examples:
        merged = Settings.model_validate(existing.model_dump() | json.loads(example))
        assert merged.command == existing.command
        assert merged.timeout_seconds == existing.timeout_seconds
        assert merged.checkpoint_folder == existing.checkpoint_folder
