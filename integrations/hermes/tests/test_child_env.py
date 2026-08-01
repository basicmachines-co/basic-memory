"""
Child processes must not inherit Hermes's Python environment.

Hermes runs on its own interpreter and exports PYTHONPATH; `bm` is a separate
uv-managed tool on a different Python. A child that inherits those variables
imports the wrong site-packages and dies on a compiled-extension mismatch
(`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`).

The last test here is the one that matters: it spawns a real subprocess with a
deliberately contaminated PYTHONPATH. Mocks cannot reproduce this class of bug,
because the failure lives in interpreter startup rather than in our own code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest


# A child that reports how importing `hermes_abi_canary` ended. It distinguishes
# "the poisoned copy was reachable" (ImportError raised by the module body) from
# "nothing was reachable" (ModuleNotFoundError from the import machinery), so the
# assertion is about which module the child could see, not about text in a log.
_PROBE = textwrap.dedent(
    """
    import importlib, sys
    try:
        importlib.import_module("hermes_abi_canary")
    except ImportError as exc:
        sys.stdout.write(type(exc).__name__)
    else:
        sys.stdout.write("imported")
    """
)


@pytest.fixture
def poisoned_path(tmp_path, monkeypatch):
    """A directory on PYTHONPATH holding a module that fails the way #1093 does."""
    pkg = tmp_path / "hermes_abi_canary"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "raise ImportError(\"No module named 'pydantic_core._pydantic_core'\")\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    return tmp_path


# ---- The sanitizer itself ----


def test_strips_parent_python_vars(bm, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")
    monkeypatch.setenv("PYTHONHOME", "/hermes/py311")
    monkeypatch.setenv("VIRTUAL_ENV", "/hermes/.venv")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "/hermes/bin/python3.11")

    cleaned = bm._clean_child_env()

    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        assert var not in cleaned


def test_keeps_everything_else(bm, monkeypatch):
    """The child still needs PATH, HOME, proxies and credentials."""
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")

    cleaned = bm._clean_child_env()

    assert cleaned["HTTPS_PROXY"] == "http://proxy.internal:3128"
    assert cleaned["PATH"] == os.environ["PATH"]


def test_sanitizes_an_explicitly_passed_env(bm):
    """The guarantee is about what the child receives, not about who built it."""
    cleaned = bm._clean_child_env({"PYTHONPATH": "/hermes/py311", "HOME": "/home/user"})

    assert "PYTHONPATH" not in cleaned
    assert cleaned["HOME"] == "/home/user"


def test_does_not_mutate_the_process_environment(bm, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")

    bm._clean_child_env()

    assert os.environ["PYTHONPATH"] == "/hermes/py311/site-packages"


# ---- Every spawn site ----


def test_actor_env_is_sanitized(bm, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")

    actor = bm._BmMcpActor(["fake-bm", "mcp"])

    assert "PYTHONPATH" not in actor._env


def test_actor_sanitizes_a_caller_supplied_env(bm):
    actor = bm._BmMcpActor(["fake-bm", "mcp"], env={"PYTHONPATH": "/hermes", "PATH": "/usr/bin"})

    assert "PYTHONPATH" not in actor._env
    assert actor._env["PATH"] == "/usr/bin"


def test_uv_install_gets_a_sanitized_env(bm, monkeypatch):
    """`uv tool install` builds the very environment the MCP server runs in."""
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/usr/local/bin/uv")
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/home/u/.local/bin/bm")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(bm.subprocess, "run", fake_run)

    bm._install_bm_via_uv()

    assert "env" in seen, "no env= passed, so the child inherits Hermes's implicitly"
    assert "PYTHONPATH" not in seen["env"]


def test_project_add_gets_a_sanitized_env(bm, monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "/hermes/py311/site-packages")
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/home/u/.local/bin/bm")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(bm.subprocess, "run", fake_run)

    provider = bm.BasicMemoryProvider()
    provider._project_path = str(tmp_path / "project")
    provider._ensure_local_project()

    assert "env" in seen, "no env= passed, so the child inherits Hermes's implicitly"
    assert "PYTHONPATH" not in seen["env"]


# ---- Real subprocess: the part mocks cannot cover ----


def test_contaminated_pythonpath_reaches_an_inherited_child(bm, poisoned_path):
    """
    Guard for the guard: prove the contamination is real before asserting it is
    gone. Without this, a sanitizer that did nothing would still look green.
    """
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=60,
    )

    assert result.stdout == "ImportError"


def test_sanitized_child_cannot_see_the_parent_path(bm, poisoned_path):
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=bm._clean_child_env(),
        timeout=60,
    )

    assert result.stdout == "ModuleNotFoundError"
