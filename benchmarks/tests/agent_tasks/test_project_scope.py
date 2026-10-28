"""Project-ID grades must match this project's identity, including aliases."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from basic_memory_benchmarks.agent_tasks.grading import GradingContext, evaluate_grader
from basic_memory_benchmarks.agent_tasks.loop import ToolOutcome, run_agent_loop
from basic_memory_benchmarks.agent_tasks.models import AgentBudget, TurnRecord
from basic_memory_benchmarks.agent_tasks.spec import ProjectIdUsed
from basic_memory_benchmarks.llm.tool_agent import ScriptedToolAgent, ToolDef

PROJECT_ID = UUID("9ff03967-bac5-4cef-9b93-d2a75f8bc721")


@pytest.mark.parametrize(
    "scope,success",
    [
        (str(PROJECT_ID), True),
        (PROJECT_ID.hex, True),
        ("project-name", False),
        ("f0000000-0000-0000-0000-000000000000", False),
        (None, False),
    ],
)
def test_only_current_project_external_id_passes(tmp_path: Path, scope: str | None, success: bool):
    db_path = tmp_path / "memory.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE project (name TEXT, external_id TEXT)")
        connection.execute("INSERT INTO project VALUES (?, ?)", ("project-name", PROJECT_ID.hex))
        connection.commit()
    finally:
        connection.close()
    ctx = GradingContext(
        final_answer="answer",
        project_dir=tmp_path,
        baseline={},
        db_path=db_path,
        project_name="project-name",
        turn_records=[
            TurnRecord(
                turn_index=0,
                kind="tool",
                tool_name="bm_read",
                project_scope=scope,
            )
        ],
    )
    assert evaluate_grader(ProjectIdUsed(r"^bm_read$"), ctx).passed is success
    ctx.turn_records = [
        TurnRecord(
            turn_index=0, kind="tool", tool_name="bm_read", project_scope=scope, is_error=True
        )
    ]
    assert not evaluate_grader(ProjectIdUsed(r"^bm_read$"), ctx).passed


@pytest.mark.parametrize("name,expected", [("bm_read", "canonical"), ("read_note", "legacy-id")])
def test_scope_audit_observes_alias_precedence(name: str, expected: str):
    model = ScriptedToolAgent(
        script={
            "tasks": {
                "read": [
                    {
                        "tool_calls": [
                            {
                                "name": name,
                                "arguments": {"project": "canonical", "project_id": "legacy-id"},
                            }
                        ]
                    },
                    {"text": "done"},
                ]
            }
        }
    )
    result = run_agent_loop(
        model=model,
        dispatch=lambda name, arguments: ToolOutcome("ok", False),
        tools=[ToolDef(name, "Read", {"type": "object"})],
        prompt="read",
        budget=AgentBudget(),
    )
    assert result.turn_records[1].project_scope == expected
    assert result.turn_records[1].argument_keys == ["project", "project_id"]
