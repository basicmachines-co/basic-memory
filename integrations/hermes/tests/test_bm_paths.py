"""Registry path and bm binary lookup for the Hermes plugin."""

from __future__ import annotations

import json
from pathlib import Path

LOCAL_BM = "/home/tester/.local/bin/bm"
HOMEBREW_BM = "/opt/homebrew/bin/bm"
USR_LOCAL_BM = "/usr/local/bin/bm"


def _write_projects(path: Path, projects: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}))


def _patch_home(monkeypatch, bm, home: Path) -> None:
    monkeypatch.setattr(bm.Path, "home", lambda: home)


def _patch_binary_lookup(
    monkeypatch,
    bm,
    *,
    which_result: str | None,
    files: set[str],
    executable: set[str],
) -> None:
    monkeypatch.setattr(bm, "which", lambda name: which_result if name == "bm" else None)
    monkeypatch.setattr(
        bm.os.path,
        "expanduser",
        lambda path: LOCAL_BM if path == "~/.local/bin/bm" else path,
    )
    monkeypatch.setattr(bm.os.path, "isfile", lambda path: path in files)
    monkeypatch.setattr(bm.os, "access", lambda path, mode: path in executable)


def test_config_dir_beats_xdg_for_project_registry(bm, monkeypatch, tmp_path):
    """BASIC_MEMORY_CONFIG_DIR wins even when an XDG registry also exists."""
    custom = tmp_path / "custom"
    xdg = tmp_path / "xdg"
    _write_projects(custom / "config.json", {"from-config-dir": {"path": "/custom"}})
    _write_projects(xdg / "basic-memory" / "config.json", {"from-xdg": {"path": "/xdg"}})
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert bm._bm_config_path() == custom / "config.json"
    assert bm._bm_known_projects() == {"from-config-dir": {"path": "/custom"}}


def test_xdg_registry_used_when_legacy_home_file_omits_project(bm, monkeypatch, tmp_path):
    """A project that exists only under XDG is visible, and the legacy file is not."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    _patch_home(monkeypatch, bm, home)
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _write_projects(home / ".basic-memory" / "config.json", {"legacy-only": {}})
    _write_projects(
        xdg / "basic-memory" / "config.json",
        {"hermes-memory": {"path": "/notes"}},
    )

    assert bm._bm_config_path() == xdg / "basic-memory" / "config.json"
    assert bm._bm_known_projects() == {"hermes-memory": {"path": "/notes"}}


def test_empty_config_dir_falls_through_to_xdg(bm, monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", "")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert bm._bm_config_path() == xdg / "basic-memory" / "config.json"


def test_config_path_defaults_to_home_and_missing_file_stays_missing(bm, monkeypatch, tmp_path):
    home = tmp_path / "home"
    _patch_home(monkeypatch, bm, home)
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    path = bm._bm_config_path()

    assert path == home / ".basic-memory" / "config.json"
    assert path.exists() is False
    assert bm._bm_known_projects() is None


def test_bm_binary_path_prefers_which_over_stale_local_bin(bm, monkeypatch):
    """PATH's bm wins while ~/.local/bin/bm still exists and is executable."""
    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result="/usr/bin/bm",
        files={LOCAL_BM, HOMEBREW_BM, USR_LOCAL_BM},
        executable={LOCAL_BM, HOMEBREW_BM, USR_LOCAL_BM},
    )

    assert bm._bm_binary_path() == "/usr/bin/bm"


def test_bm_binary_path_falls_back_homebrew_then_usr_local_then_local(bm, monkeypatch):
    """With nothing on PATH, fixed locations run homebrew, /usr/local, then ~/.local."""
    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result=None,
        files={HOMEBREW_BM, USR_LOCAL_BM, LOCAL_BM},
        executable={HOMEBREW_BM, USR_LOCAL_BM, LOCAL_BM},
    )
    assert bm._bm_binary_path() == HOMEBREW_BM

    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result=None,
        files={USR_LOCAL_BM, LOCAL_BM},
        executable={USR_LOCAL_BM, LOCAL_BM},
    )
    assert bm._bm_binary_path() == USR_LOCAL_BM

    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result=None,
        files={LOCAL_BM},
        executable={LOCAL_BM},
    )
    assert bm._bm_binary_path() == LOCAL_BM


def test_bm_binary_path_skips_non_executable_and_returns_none(bm, monkeypatch):
    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result=None,
        files={HOMEBREW_BM, USR_LOCAL_BM},
        executable={USR_LOCAL_BM},
    )
    assert bm._bm_binary_path() == USR_LOCAL_BM

    _patch_binary_lookup(
        monkeypatch,
        bm,
        which_result=None,
        files=set(),
        executable=set(),
    )
    assert bm._bm_binary_path() is None
