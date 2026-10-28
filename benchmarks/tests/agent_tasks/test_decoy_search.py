"""The extra web server is visible to the model but never dispatched to BM."""

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory_benchmarks.agent_tasks import driver
from basic_memory_benchmarks.agent_tasks.driver import TaskProject, run_agent_tasks
from basic_memory_benchmarks.agent_tasks.loop import ToolOutcome
from basic_memory_benchmarks.agent_tasks.models import AgentTasksConfig
from basic_memory_benchmarks.agent_tasks.surfaces import BM_SURFACE
from basic_memory_benchmarks.agent_tasks.tasks import ORPHAN_PERMALINKS
from basic_memory_benchmarks.llm.tool_agent import ScriptedToolAgent, ToolDef

CORPUS = Path(__file__).parents[2] / "benchmarks/datasets/agent-tasks/corpus"


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> None:
        pass

    def tools(self) -> list[ToolDef]:
        return [
            ToolDef(name, "memory tool", {"type": "object"}) for name in BM_SURFACE.tool_allowlist
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        return ToolOutcome("memory result", False)

    def stop(self) -> None:
        pass


@pytest.mark.parametrize(
    "model_spec,resolved_project",
    [
        ("scripted:fixture", "fixture-project"),
        ("openai-compat:fixture@https://example.invalid", "{project}"),
    ],
)
def test_decoy_calls_are_counted_and_real_model_arguments_are_not_rewritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_spec: str,
    resolved_project: str,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver, "resolve_clean_checkout_sha", lambda path: "fixture-sha")
    monkeypatch.setattr(driver, "_bm_version", lambda prefix, env: "fixture-version")
    monkeypatch.setattr(
        driver, "_prepare_task_project", lambda **kwargs: TaskProject("fixture-project", tmp_path)
    )
    session = RecordingSession()
    model = ScriptedToolAgent(
        script={
            "tasks": {
                "orphan notes": [
                    {"tool_calls": [{"name": "web_search", "arguments": {"query": "orphans"}}]},
                    {
                        "tool_calls": [
                            {
                                "name": "bm_search",
                                "arguments": {"query": "redis", "project": "{project}"},
                            }
                        ]
                    },
                    {
                        "text": "```json\n"
                        + json.dumps({"permalinks": sorted(ORPHAN_PERMALINKS)})
                        + "\n```"
                    },
                ]
            }
        }
    )
    config = AgentTasksConfig(
        run_id="decoy-unit",
        surfaces=["bm"],
        task_ids=["curate-orphans"],
        model_spec=model_spec,
        bm_local_path=str(tmp_path),
        corpus_dir=str(CORPUS),
        decoy_search=True,
        allow_surface_skip=False,
    )
    run = run_agent_tasks(
        config, model_factory=lambda spec: model, session_factory=lambda runtime: session
    )
    assert session.calls == [("bm_search", {"query": "redis", "project": resolved_project})]
    manifest = json.loads((run / "manifest.json").read_text())
    echo = manifest["surfaces"][0]
    assert echo["injected_tools"] == ["web_search"]
    assert "web_search" not in echo["observed_tools"]
    assert echo["tool_allowlist"][-1] == echo["tool_definitions"][-1]["name"] == "web_search"
    summary = json.loads((run / "agent-tasks-summary.json").read_text())["surfaces"][0]
    assert summary["decoy_tool_calls"] == 1
    assert summary["tasks_passed"] == 1
