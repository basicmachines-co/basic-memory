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


def test_codex_plugin_hooks_use_clear_portable_runtime_patterns() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pre_compact_sh = (repo_root / "plugins/codex/hooks/pre-compact.sh").read_text(encoding="utf-8")
    pre_compact_py = (repo_root / "plugins/codex/hooks/pre-compact.py").read_text(encoding="utf-8")
    session_start_sh = (repo_root / "plugins/codex/hooks/session-start.sh").read_text(
        encoding="utf-8"
    )
    session_start_py = (repo_root / "plugins/codex/hooks/session-start.py").read_text(
        encoding="utf-8"
    )

    assert "python3 <<'PY'" not in pre_compact_sh
    assert "python3 <<'PY'" not in session_start_sh
    assert 'uv run --script "$script_dir/pre-compact.py"' in pre_compact_sh
    assert 'uv run --script "$script_dir/session-start.py"' in session_start_sh
    assert pre_compact_py.startswith("#!/usr/bin/env -S uv run --script\n")
    assert session_start_py.startswith("#!/usr/bin/env -S uv run --script\n")
    assert "from datetime import datetime, timezone" in pre_compact_py
    assert "datetime.now(timezone.utc)" in pre_compact_py
    assert 'now.isoformat(timespec="seconds")' in pre_compact_py
    assert "if r not in codex_rows" not in session_start_py


def test_codex_plugin_docs_explain_global_install_and_repo_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "plugins/codex/README.md").read_text(encoding="utf-8")

    assert "## Install" in readme
    assert 'codex plugin marketplace add "$(git rev-parse --show-toplevel)"' in readme
    assert "codex plugin add codex@basic-memory-local" in readme
    assert "Plugin installation is user-level in Codex" in readme
    assert "Each repository still needs its own `.codex/basic-memory.json`" in readme


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


def test_pr_create_skill_documents_optional_infographic_theme_arg() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill = (repo_root / ".agents/skills/pr-create/SKILL.md").read_text(encoding="utf-8")
    skill_flat = " ".join(skill.split())

    assert "## How To Use" in skill
    assert "$pr-create" in skill
    assert "<theme>" in skill
    assert '$pr-create "Italian movie poster"' in skill
    assert '$pr-create "80\'s action movies"' in skill
    assert "<!-- BM_INFOGRAPHIC_THEME:start -->" in skill
    assert "<!-- BM_INFOGRAPHIC_THEME:end -->" in skill
    assert "<!-- BM_INFOGRAPHIC_PROVENANCE:start -->" in skill
    assert "BM Bossbot Approval" in skill
    assert "selected visual direction" in skill_flat
    assert "never merges" in skill
    assert "non-gating" in skill
