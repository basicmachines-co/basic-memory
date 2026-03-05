"""Tests for v2 graph intelligence and FCM routers."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_graph_lineage_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/graph/lineage",
        json={"start": "memory://specs/search"},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["root", "paths", "generated_at"]).issubset(data.keys())
    assert data["root"]["id"] == "specs/search"
    assert isinstance(data["paths"], list)


@pytest.mark.asyncio
async def test_graph_impact_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/graph/impact",
        json={"target": "memory://specs/search", "horizon": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["target", "affected", "summary"]).issubset(data.keys())
    assert data["summary"]["total_considered"] >= data["summary"]["total_returned"]


@pytest.mark.asyncio
async def test_graph_health_contract(client: AsyncClient, v2_project_url: str):
    response = await client.get(
        f"{v2_project_url}/graph/health",
        params={"scope": "specs", "timeframe": "30d"},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["metrics", "issues", "computed_at"]).issubset(data.keys())
    assert "orphan_rate" in data["metrics"]


@pytest.mark.asyncio
async def test_graph_reindex_schedules_task(
    client: AsyncClient,
    v2_project_url: str,
    task_scheduler_spy: list[dict[str, object]],
):
    response = await client.post(
        f"{v2_project_url}/graph/reindex",
        json={"mode": "full", "reason": "contract test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["job_id"]

    assert task_scheduler_spy
    last = task_scheduler_spy[-1]
    assert last["task_name"] == "reindex_graph_project"
    assert last["payload"]["mode"] == "full"
    assert last["payload"]["reason"] == "contract test"


@pytest.mark.asyncio
async def test_fcm_simulate_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/fcm/simulate",
        json={"actions": [{"node_id": "test-node", "delta": 0.2}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["baseline", "projected", "deltas", "stability", "confidence"]).issubset(data.keys())
    assert data["stability"]["converged"] is True


@pytest.mark.asyncio
async def test_fcm_rank_actions_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/fcm/rank-actions",
        json={"goal": "reduce-regressions", "top_k": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["goal", "recommendations"]).issubset(data.keys())
    assert len(data["recommendations"]) <= 2


@pytest.mark.asyncio
async def test_fcm_import_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/fcm/import",
        json={"source": "/tmp/model.csv", "format": "csv_bundle_v1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["import_id", "nodes_loaded", "edges_loaded", "warnings", "errors"]).issubset(
        data.keys()
    )


@pytest.mark.asyncio
async def test_fcm_export_contract(client: AsyncClient, v2_project_url: str):
    response = await client.post(
        f"{v2_project_url}/fcm/export",
        json={"format": "csv_bundle_v1", "selection": {"scope": "all"}},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(["export_id", "format", "files", "node_count", "edge_count"]).issubset(data.keys())
    assert len(data["files"]) == 2
