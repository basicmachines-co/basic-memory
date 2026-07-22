import json
import re
import subprocess
from pathlib import Path


def test_codex_plugin_mcp_config_is_tracked_and_not_ignored() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rel_path = "plugins/codex/.mcp.json"

    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", rel_path],
        cwd=repo_root,
        check=False,
    )
    assert ignored.returncode == 1

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel_path],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr


def test_codex_plugin_hooks_are_zero_logic_uv_scripts() -> None:
    # The plugin ships configuration plus launchers only: the hook bodies live
    # in the basic-memory package behind `bm hook` (SPEC-55); each launcher is
    # a self-contained PEP 723 uv script with no resolver logic of its own.
    repo_root = Path(__file__).resolve().parents[1]
    hooks_dir = repo_root / "plugins/codex/hooks"

    assert not (hooks_dir / "session-start.sh").exists()
    assert not (hooks_dir / "pre-compact.sh").exists()
    dependency_refs: set[str] = set()
    for script, verb in (
        ("session_start.py", "session-start"),
        ("pre_compact.py", "pre-compact"),
    ):
        text = (hooks_dir / script).read_text(encoding="utf-8")
        assert "# /// script" in text
        refs = re.findall(
            r'"basic-memory @ git\+https://github\.com/'
            r'basicmachines-co/basic-memory@([^"]+)"',
            text,
        )
        assert len(refs) == 1
        dependency_refs.add(refs[0])
        assert f'VERB = "{verb}"' in text
        assert 'HARNESS = "codex"' in text
    assert len(dependency_refs) == 1


def test_release_recipes_pin_codex_hooks_to_the_release_tag() -> None:
    justfile = (Path(__file__).resolve().parents[1] / "justfile").read_text(encoding="utf-8")

    assert justfile.count('just set-codex-hook-version "{{version}}"') == 2
    assert 'just set-codex-hook-version "$(git rev-parse HEAD)"' not in justfile
    assert "uv add --script plugins/codex/hooks/stop.py" not in justfile


def test_codex_plugin_marketplace_identity() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    marketplace = json.loads(
        (repo_root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )

    assert marketplace["name"] == "basic-memory"
    assert marketplace["interface"]["displayName"] == "Basic Memory"
    assert marketplace["plugins"][0]["name"] == "codex"


def test_codex_plugin_docs_explain_global_install_and_repo_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "plugins/codex/README.md").read_text(encoding="utf-8")

    assert "## Install" in readme
    assert 'codex plugin marketplace add "$(git rev-parse --show-toplevel)"' in readme
    assert "codex plugin add codex@basic-memory" in readme
    assert "Plugin installation is user-level in Codex" in readme
    assert "Selecting only `plugins/codex` omits" in readme
    assert "marketplace file should not" in readme
    assert "Configuration can live at user level in `~/.codex/basic-memory.json`" in readme
    assert "the nearest project file overrides only the keys it declares" in readme
    assert "keep both the profile and checkout-specific repository" in readme


def test_user_level_coding_profile_stays_with_repository_override() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "plugins/codex/README.md").read_text(encoding="utf-8")
    setup = (repo_root / "plugins/codex/skills/bm-setup/SKILL.md").read_text(encoding="utf-8")

    readme_blocks = re.findall(r"```json\n(.*?)\n```", readme, flags=re.DOTALL)
    shared_settings = json.loads(readme_blocks[0])["basicMemory"]
    project_settings = json.loads(readme_blocks[1])["basicMemory"]

    assert "sessionProfile" not in shared_settings
    assert project_settings == {
        "sessionProfile": "coding",
        "repository": "owner/repo",
    }
    assert "omit `sessionProfile` from the shared user file" in setup
    assert '"sessionProfile": "coding",\n    "repository": "owner/name"' in setup


def test_coding_session_schema_is_shared_across_host_plugins() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    codex_schema = (repo_root / "plugins/codex/schemas/coding-session.md").read_text(
        encoding="utf-8"
    )
    claude_schema = (repo_root / "plugins/claude-code/schemas/coding-session.md").read_text(
        encoding="utf-8"
    )

    assert codex_schema == claude_schema


def test_coding_checkpoint_skills_quote_pull_request_numbers() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for plugin in ("codex", "claude-code"):
        skill = (repo_root / "plugins" / plugin / "skills/bm-checkpoint/SKILL.md").read_text(
            encoding="utf-8"
        )
        assert 'pull_request_number: "123"' in skill


def test_bm_checkpoint_tells_a_story_and_uses_graph_semantics() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill = (repo_root / "plugins/codex/skills/bm-checkpoint/SKILL.md").read_text(encoding="utf-8")
    writing = (repo_root / "plugins/codex/skills/bm-writing/SKILL.md").read_text(encoding="utf-8")
    decide = (repo_root / "plugins/codex/skills/bm-decide/SKILL.md").read_text(encoding="utf-8")
    remember = (repo_root / "plugins/codex/skills/bm-remember/SKILL.md").read_text(encoding="utf-8")
    schema = (repo_root / "plugins/codex/schemas/codex-session.md").read_text(encoding="utf-8")

    assert ".github/basic-memory" not in skill
    assert "Apply the `bm-writing` skill" in skill
    assert "Apply the `bm-writing` skill" in decide
    assert "Apply the `bm-writing` skill" in remember
    assert "A checkpoint is a durable handoff, not a status dump" in skill
    assert "Begin the body with `# <exact note title>`" in skill
    assert "username: <current username>" in skill
    assert "hostname: <current hostname>" in skill
    assert "- `[decision]` for each decision made or preserved" in skill
    assert "- `[next_step]` for the next concrete action" in skill
    assert "- relates_to [[Exact existing note title]]" in skill
    assert "Never write `[relates_to]` or a bare `memory://` URL as an observation" in skill
    assert "\n- Decisions\n" not in skill
    assert "username?: string" in schema
    assert "hostname?: string" in schema
    assert "intentionally user-customizable" in writing
    assert "problem -> approach -> current state and impact" in writing
    assert "- relation_type [[Target Note]]" in writing
    assert "Do not invent intent, impact, verification, decisions, or drama" in writing


def test_codex_checkpoint_applies_accumulated_redaction_before_write() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill = (repo_root / "plugins/codex/skills/bm-checkpoint/SKILL.md").read_text(encoding="utf-8")

    assert "## Privacy Gate" in skill
    assert "`redactKeys` and `redactPaths` accumulate" in skill
    assert "Scrub **every string** passed to `write_note`" in skill
    assert "Do not omit schema-required path fields; use the marker" in skill
    assert "[REDACTED_PATH]" in skill
    assert "[REDACTED]" in skill
    assert "fail closed: skip the checkpoint" in skill
    assert "Do not fall back to an unredacted note" in skill


def test_infographics_skill_keeps_weekly_contract_and_bm_style_pool() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill = (repo_root / ".agents/skills/infographics/SKILL.md").read_text(encoding="utf-8")
    style_balance = (
        repo_root / ".agents/skills/infographics/references/style-balance.md"
    ).read_text(encoding="utf-8")
    prompt_blueprint = (
        repo_root / ".agents/skills/infographics/references/prompt-blueprint.md"
    ).read_text(encoding="utf-8")
    skill_flat = " ".join(skill.split())

    assert "Weekly image" in skill
    assert "2-Week Retro window" in skill
    assert "docs/assets/infographics/<year>-w<start-week>-w<end-week>.webp" in skill
    assert (
        "docs/assets/infographics/<start-year>-w<start-week>-<end-year>-w<end-week>.webp" in skill
    )
    assert "computer science college textbooks" in skill
    assert "classic literature subjects" in skill
    assert "Metal, Hard Rock, Punk, techno, soul, reggae" in skill
    assert "Star Wars inspired knockoff" in skill
    assert "WWII propaganda posters" in skill
    assert "Italian movie posters" in skill
    assert "space exploration and astronomy" in skill
    assert "paintings" in skill
    assert "abstract painting" in skill
    assert "classical landscape" in skill
    assert "Remington-inspired" in skill
    assert "Rembrandt-inspired" in skill
    assert "classic black-and-white photography" in skill
    assert "documentary" in skill
    assert "editorial photo essay" in skill
    assert "80's action movies" in skill
    assert "practical explosions" in skill
    assert "no direct actor likenesses" in skill
    assert "poster, scene, tableau, cover image" in skill_flat
    assert "image-first" in skill
    assert "--print-prompt" in skill
    assert "--dry-run" in skill
    assert "--visual-format" not in skill
    assert "--provenance-output" in skill
    assert "BM_INFOGRAPHIC_PROVENANCE:start" in skill
    assert "Image prompt sent to" not in skill
    assert "revised prompt" not in skill
    assert "star charts" in style_balance
    assert "editorial scene" in style_balance
    assert "painting, photograph" in style_balance
    assert "copyrighted characters, logos, or named fictional universes" in skill
    assert "retro game or classic app aesthetic" not in skill
    assert "BM style category" in style_balance
    assert "Chosen image form" in prompt_blueprint
    assert "Chosen BM style category" in prompt_blueprint


def test_pr_create_skill_delegates_to_current_pr_workflow() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill = (repo_root / ".agents/skills/pr-create/SKILL.md").read_text(encoding="utf-8")
    skill_flat = " ".join(skill.split())

    assert "## Compatibility" in skill
    assert "## Workflow" in skill
    assert "$pr-create" in skill
    assert "`pull-request`" in skill
    assert "`pr-description`" in skill
    assert "`pr-review-loop`" in skill
    assert "`fix-pr-issues`" in skill
    assert "automatic PR-infographic workflow has been retired" in skill_flat
    assert "Do not wait for the deleted `BM Bossbot Approval` status" in skill_flat
    assert "<!-- BM_INFOGRAPHIC_THEME:start -->" not in skill
    assert "<!-- BM_INFOGRAPHIC_PROVENANCE:start -->" not in skill
    assert "never merges" in skill
    assert "Do not enable auto-merge" in skill
    assert "current-head gate" in skill
