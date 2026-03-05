"""Tests for graph intelligence MCP tools."""

import pytest

from basic_memory.mcp.tools import (
    fcm_export_model,
    fcm_import_model,
    fcm_rank_actions,
    fcm_simulate,
    graph_health,
    graph_impact,
    graph_lineage,
    graph_reindex,
)


@pytest.mark.asyncio
async def test_graph_lineage_json_and_text_modes(app, test_project):
    json_result = await graph_lineage(
        start="memory://specs/search",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(json_result, dict)
    assert set(["root", "paths", "generated_at"]).issubset(json_result.keys())

    text_result = await graph_lineage(
        start="memory://specs/search",
        project=test_project.name,
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "Graph Lineage" in text_result


@pytest.mark.asyncio
async def test_graph_impact_and_health(app, test_project):
    impact = await graph_impact(
        target="memory://specs/search",
        horizon=2,
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(impact, dict)
    assert set(["target", "affected", "summary"]).issubset(impact.keys())

    health = await graph_health(
        scope="specs",
        timeframe="30d",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(health, dict)
    assert set(["metrics", "issues", "computed_at"]).issubset(health.keys())


@pytest.mark.asyncio
async def test_graph_reindex(app, test_project):
    result = await graph_reindex(project=test_project.name, output_format="json")
    assert isinstance(result, dict)
    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_fcm_simulate_and_rank_actions(app, test_project):
    simulation = await fcm_simulate(
        actions=[{"node_id": "n1", "delta": 0.2}],
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(simulation, dict)
    assert set(["baseline", "projected", "deltas", "stability", "confidence"]).issubset(
        simulation.keys()
    )

    ranking = await fcm_rank_actions(
        goal="reduce-regressions",
        top_k=2,
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(ranking, dict)
    assert set(["goal", "recommendations"]).issubset(ranking.keys())
    assert len(ranking["recommendations"]) <= 2


@pytest.mark.asyncio
async def test_fcm_import_export_json_and_text(app, test_project):
    imported = await fcm_import_model(
        source="/tmp/model.csv",
        format="csv_bundle_v1",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(imported, dict)
    assert "import_id" in imported

    exported_json = await fcm_export_model(
        format="csv_bundle_v1",
        selection={"scope": "all"},
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(exported_json, dict)
    assert set(["export_id", "files", "node_count", "edge_count"]).issubset(exported_json.keys())

    exported_text = await fcm_export_model(
        format="csv_bundle_v1",
        selection={"scope": "all"},
        project=test_project.name,
        output_format="text",
    )
    assert isinstance(exported_text, str)
    assert "FCM Export" in exported_text
