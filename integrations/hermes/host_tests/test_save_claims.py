"""Local host-contract proof; no model or Basic Memory server calls."""

import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import hermes_cli.plugins as plugins
from agent.turn_finalizer import _apply_output_hooks
from model_tools import _emit_post_tool_call_hook
from plugins.memory import _load_provider_from_dir


@pytest.mark.parametrize(
    "result, corrected",
    [(None, True), ('{"error":"offline"}', True), ('{"permalink":"notes/test"}', False)],
)
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize(
    "confirmation",
    ["Saved to Basic Memory.", "Got it. I have recorded that:\n- The date is October 3rd."],
)
def test_real_dispatch_and_finalizer(
    tmp_path, monkeypatch, result, corrected, active, confirmation
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    directory = tmp_path / "plugins" / "basic-memory"
    directory.mkdir(parents=True)
    shutil.copyfile(
        Path(__file__).parents[1] / "save_claims.py",
        directory / "save_claims.py",
    )
    source = Path(__file__).parents[1]
    for filename in ["__init__.py", "plugin.yaml"]:
        shutil.copyfile(source / filename, directory / filename)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {"enabled": ["basic-memory"]},
                "memory": {"provider": "basic-memory" if active else "builtin"},
            }
        )
    )
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    manager.discover_and_load()
    provider = _load_provider_from_dir(directory)
    assert provider is not None
    assert manager.has_hook("transform_llm_output")
    manager.invoke_hook("pre_llm_call", session_id="s1", turn_id="t1", user_message="Remember this")
    if result is not None:
        _emit_post_tool_call_hook(
            function_name="bm_write", function_args={}, result=result, session_id="s1", turn_id="t1"
        )
    response, transformed, original = _apply_output_hooks(
        SimpleNamespace(session_id="s1", model="test"),
        confirmation,
        logging.getLogger(__name__),
        platform="cli",
        effective_task_id="task",
        turn_id="t1",
        original_user_message="Remember this",
        messages=[],
    )
    corrected = corrected and active
    assert transformed is corrected
    assert ("unverified" in response) is corrected
    assert original == (confirmation if corrected else None)
