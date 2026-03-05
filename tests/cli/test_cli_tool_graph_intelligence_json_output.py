"""Tests for graph/FCM CLI tool JSON passthrough commands."""

import json
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


@patch(
    "basic_memory.cli.commands.tool.mcp_graph_lineage",
    new_callable=AsyncMock,
    return_value={
        "root": {"id": "specs/search"},
        "paths": [],
        "generated_at": "2026-03-05T00:00:00Z",
    },
)
def test_graph_lineage_json_output(mock_tool):
    result = runner.invoke(cli_app, ["tool", "graph-lineage", "memory://specs/search"])
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["root"]["id"] == "specs/search"
    assert mock_tool.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.cli.commands.tool.mcp_graph_impact",
    new_callable=AsyncMock,
    return_value={
        "target": {"id": "specs/search", "title": "specs/search"},
        "affected": [],
        "summary": {"total_considered": 0, "total_returned": 0},
    },
)
def test_graph_impact_passthrough(mock_tool):
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "graph-impact",
            "memory://specs/search",
            "--horizon",
            "3",
            "--relation-filter",
            "depends_on",
        ],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_tool.call_args.kwargs["horizon"] == 3
    assert mock_tool.call_args.kwargs["relation_filters"] == ["depends_on"]
    assert mock_tool.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.cli.commands.tool.mcp_graph_health",
    new_callable=AsyncMock,
    return_value={
        "metrics": {
            "orphan_rate": 0.0,
            "stale_central_nodes": 0,
            "overloaded_hubs": 0,
            "contradiction_candidates": 0,
        },
        "issues": [],
        "computed_at": "2026-03-05T00:00:00Z",
    },
)
def test_graph_health_json_output(mock_tool):
    result = runner.invoke(
        cli_app,
        ["tool", "graph-health", "--scope", "specs", "--timeframe", "30d"],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "metrics" in data
    assert mock_tool.call_args.kwargs["scope"] == "specs"
    assert mock_tool.call_args.kwargs["timeframe"] == "30d"


@patch(
    "basic_memory.cli.commands.tool.mcp_fcm_simulate",
    new_callable=AsyncMock,
    return_value={
        "baseline": [],
        "projected": [],
        "deltas": [],
        "stability": {"converged": True, "iterations_used": 1, "residual": 0.0},
        "confidence": 0.5,
    },
)
def test_fcm_simulate_json_output(mock_tool):
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "fcm-simulate",
            "--actions-json",
            '[{"node_id":"n1","delta":0.2}]',
            "--scenario-json",
            '{"steps":8}',
        ],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_tool.call_args.kwargs["actions"] == [{"node_id": "n1", "delta": 0.2}]
    assert mock_tool.call_args.kwargs["scenario"] == {"steps": 8}


def test_fcm_simulate_invalid_actions_json():
    result = runner.invoke(
        cli_app,
        ["tool", "fcm-simulate", "--actions-json", '{"node_id":"n1","delta":0.2}'],
    )
    assert result.exit_code == 1
    assert "expected a JSON array" in result.output


@patch(
    "basic_memory.cli.commands.tool.mcp_fcm_rank_actions",
    new_callable=AsyncMock,
    return_value={"goal": {"node_id": "g1", "label": "g1"}, "recommendations": []},
)
def test_fcm_rank_actions_passthrough(mock_tool):
    result = runner.invoke(
        cli_app,
        ["tool", "fcm-rank-actions", "g1", "--constraints-json", '{"required_tags":["risk"]}'],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_tool.call_args.kwargs["constraints"] == {"required_tags": ["risk"]}
    assert mock_tool.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.cli.commands.tool.mcp_fcm_import_model",
    new_callable=AsyncMock,
    return_value={
        "import_id": "imp-1",
        "nodes_loaded": 0,
        "edges_loaded": 0,
        "warnings": [],
        "errors": [],
    },
)
def test_fcm_import_model_json_output(mock_tool):
    result = runner.invoke(
        cli_app,
        ["tool", "fcm-import-model", "/tmp/model.csv", "--format", "csv_bundle_v1"],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["import_id"] == "imp-1"
    assert mock_tool.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.cli.commands.tool.mcp_fcm_export_model",
    new_callable=AsyncMock,
    return_value={
        "export_id": "exp-1",
        "format": "csv_bundle_v1",
        "files": [],
        "node_count": 0,
        "edge_count": 0,
    },
)
def test_fcm_export_model_json_output(mock_tool):
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "fcm-export-model",
            "--format",
            "csv_bundle_v1",
            "--selection-json",
            '{"scope":"all"}',
        ],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["export_id"] == "exp-1"
    assert mock_tool.call_args.kwargs["selection"] == {"scope": "all"}
