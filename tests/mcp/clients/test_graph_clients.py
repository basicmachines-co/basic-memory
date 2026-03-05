"""Tests for graph and FCM typed clients."""

from unittest.mock import MagicMock

import pytest

from basic_memory.mcp.clients import FCMClient, GraphClient


class TestGraphClient:
    def test_init(self):
        mock_http = MagicMock()
        client = GraphClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/graph"

    @pytest.mark.asyncio
    async def test_lineage(self, monkeypatch):
        from basic_memory.mcp.clients import graph as graph_mod
        from basic_memory.schemas.graph_intelligence import GraphLineageRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "root": {"id": "specs/search", "title": "specs/search", "permalink": "specs/search"},
            "paths": [],
            "generated_at": "2026-03-05T00:00:00+00:00",
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/graph/lineage" in url
            return mock_response

        monkeypatch.setattr(graph_mod, "call_post", mock_call_post)

        client = GraphClient(MagicMock(), "proj-123")
        result = await client.lineage(GraphLineageRequest(start="memory://specs/search"))
        assert result.root.id == "specs/search"

    @pytest.mark.asyncio
    async def test_impact(self, monkeypatch):
        from basic_memory.mcp.clients import graph as graph_mod
        from basic_memory.schemas.graph_intelligence import GraphImpactRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "target": {"id": "specs/search", "title": "specs/search"},
            "affected": [],
            "summary": {"total_considered": 0, "total_returned": 0},
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/graph/impact" in url
            return mock_response

        monkeypatch.setattr(graph_mod, "call_post", mock_call_post)

        client = GraphClient(MagicMock(), "proj-123")
        result = await client.impact(GraphImpactRequest(target="memory://specs/search", horizon=2))
        assert result.summary.total_returned == 0

    @pytest.mark.asyncio
    async def test_health(self, monkeypatch):
        from basic_memory.mcp.clients import graph as graph_mod

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "metrics": {
                "orphan_rate": 0.0,
                "stale_central_nodes": 0,
                "overloaded_hubs": 0,
                "contradiction_candidates": 0,
            },
            "issues": [],
            "computed_at": "2026-03-05T00:00:00+00:00",
        }

        async def mock_call_get(client, url, **kwargs):
            assert "/v2/projects/proj-123/graph/health" in url
            assert kwargs["params"]["scope"] == "specs"
            return mock_response

        monkeypatch.setattr(graph_mod, "call_get", mock_call_get)

        client = GraphClient(MagicMock(), "proj-123")
        result = await client.health(scope="specs", timeframe="30d")
        assert result.metrics.orphan_rate == 0.0

    @pytest.mark.asyncio
    async def test_reindex(self, monkeypatch):
        from basic_memory.mcp.clients import graph as graph_mod
        from basic_memory.schemas.graph_intelligence import GraphReindexRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "queued",
            "scheduled_at": "2026-03-05T00:00:00+00:00",
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/graph/reindex" in url
            return mock_response

        monkeypatch.setattr(graph_mod, "call_post", mock_call_post)

        client = GraphClient(MagicMock(), "proj-123")
        result = await client.reindex(GraphReindexRequest(mode="full"))
        assert result.status == "queued"


class TestFCMClient:
    def test_init(self):
        mock_http = MagicMock()
        client = FCMClient(mock_http, "project-123")
        assert client.http_client is mock_http
        assert client.project_id == "project-123"
        assert client._base_path == "/v2/projects/project-123/fcm"

    @pytest.mark.asyncio
    async def test_simulate(self, monkeypatch):
        from basic_memory.mcp.clients import fcm as fcm_mod
        from basic_memory.schemas.graph_intelligence import FCMSimulateRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "baseline": [{"node_id": "n1", "state": 0.0}],
            "projected": [{"node_id": "n1", "state": 0.2}],
            "deltas": [{"node_id": "n1", "delta": 0.2}],
            "stability": {"converged": True, "iterations_used": 3, "residual": 0.0},
            "confidence": 0.5,
            "explanations": [],
            "evidence_refs": [],
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/fcm/simulate" in url
            return mock_response

        monkeypatch.setattr(fcm_mod, "call_post", mock_call_post)

        request = FCMSimulateRequest(actions=[{"node_id": "n1", "delta": 0.2}])
        result = await FCMClient(MagicMock(), "proj-123").simulate(request)
        assert result.stability.converged is True

    @pytest.mark.asyncio
    async def test_rank_actions(self, monkeypatch):
        from basic_memory.mcp.clients import fcm as fcm_mod
        from basic_memory.schemas.graph_intelligence import FCMRankActionsRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "goal": {"node_id": "g1", "label": "g1"},
            "recommendations": [],
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/fcm/rank-actions" in url
            return mock_response

        monkeypatch.setattr(fcm_mod, "call_post", mock_call_post)

        request = FCMRankActionsRequest(goal="g1")
        result = await FCMClient(MagicMock(), "proj-123").rank_actions(request)
        assert result.goal.node_id == "g1"

    @pytest.mark.asyncio
    async def test_import_model(self, monkeypatch):
        from basic_memory.mcp.clients import fcm as fcm_mod
        from basic_memory.schemas.graph_intelligence import FCMImportRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "import_id": "imp-1",
            "nodes_loaded": 0,
            "edges_loaded": 0,
            "warnings": [],
            "errors": [],
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/fcm/import" in url
            return mock_response

        monkeypatch.setattr(fcm_mod, "call_post", mock_call_post)

        request = FCMImportRequest(source="/tmp/model.csv")
        result = await FCMClient(MagicMock(), "proj-123").import_model(request)
        assert result.import_id == "imp-1"

    @pytest.mark.asyncio
    async def test_export_model(self, monkeypatch):
        from basic_memory.mcp.clients import fcm as fcm_mod
        from basic_memory.schemas.graph_intelligence import FCMExportRequest

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "export_id": "exp-1",
            "format": "csv_bundle_v1",
            "files": [
                {"name": "nodes.csv", "path": "/tmp/nodes.csv"},
                {"name": "edges.csv", "path": "/tmp/edges.csv"},
            ],
            "node_count": 0,
            "edge_count": 0,
        }

        async def mock_call_post(client, url, **kwargs):
            assert "/v2/projects/proj-123/fcm/export" in url
            return mock_response

        monkeypatch.setattr(fcm_mod, "call_post", mock_call_post)

        request = FCMExportRequest()
        result = await FCMClient(MagicMock(), "proj-123").export_model(request)
        assert result.format == "csv_bundle_v1"
