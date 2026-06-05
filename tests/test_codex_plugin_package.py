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
    pre_compact = (repo_root / "plugins/codex/hooks/pre-compact.sh").read_text(encoding="utf-8")
    session_start = (repo_root / "plugins/codex/hooks/session-start.sh").read_text(encoding="utf-8")

    assert "from datetime import datetime, timezone" in pre_compact
    assert "datetime.now(timezone.utc)" in pre_compact
    assert 'now.isoformat(timespec="seconds")' in pre_compact
    assert "if r not in codex_rows" not in session_start


def test_codex_plugin_docs_explain_global_install_and_repo_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "plugins/codex/README.md").read_text(encoding="utf-8")

    assert "Plugin installation is user-level in Codex" in readme
    assert "The `.codex/basic-memory.json` file is still per repository" in readme
