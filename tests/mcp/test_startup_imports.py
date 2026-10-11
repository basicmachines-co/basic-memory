"""Fresh interpreters keep conftest's API imports out of startup regressions."""

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.parametrize("cloud", [False, True], ids=["local", "cloud"])
@pytest.mark.parametrize(
    "imports",
    [
        "import basic_memory.api.container",
        "import basic_memory.cli.main\n"
        "import basic_memory.mcp.tools\n"
        "import basic_memory.mcp.prompts\n"
        "import basic_memory.mcp.resources",
    ],
    ids=["container", "mcp-startup"],
)
def test_startup_does_not_import_api_app(tmp_path: Path, imports: str, cloud: bool) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BASIC_MEMORY_") and key != "PYTEST_CURRENT_TEST"
    }
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        BASIC_MEMORY_HOME=str(tmp_path / "basic-memory"),
        BASIC_MEMORY_CONFIG_DIR=str(tmp_path / "config"),
    )
    if cloud:
        env.update(BASIC_MEMORY_CLOUD_MODE="true", BASIC_MEMORY_FORCE_CLOUD="true")
    script = (
        "import sys\nsys.argv = ['bm', 'mcp']\n"
        + imports
        + dedent("""

        heavy = [name for name in sys.modules
                 if name == 'basic_memory.api.app'
                 or name.startswith('basic_memory.api.v2.routers')]
        assert not heavy, heavy
        """)
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("direct_import_first", [False, True], ids=["package", "submodule"])
def test_explicit_api_app_import(tmp_path: Path, direct_import_first: bool) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BASIC_MEMORY_") and key != "PYTEST_CURRENT_TEST"
    }
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        BASIC_MEMORY_HOME=str(tmp_path / "basic-memory"),
        BASIC_MEMORY_CONFIG_DIR=str(tmp_path / "config"),
    )
    script = dedent(f"""
        import importlib
        import sys
        import basic_memory.api as api

        try:
            api.unknown_attribute
        except AttributeError:
            pass
        else:
            raise AssertionError('Unknown attributes must raise AttributeError')
        assert 'basic_memory.api.app' not in sys.modules

        if {direct_import_first!r}:
            from basic_memory.api.app import app
        else:
            from basic_memory.api import app

        assert api.app is app
        from basic_memory.api import app as repeated_app
        assert repeated_app is app

        from fastapi import FastAPI
        assert isinstance(app, FastAPI)
        assert app.routes
        assert importlib.import_module('basic_memory.api.app').app is app
        from basic_memory.api.container import ApiContainer
        assert importlib.import_module('basic_memory.api.app').app is app
        """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
