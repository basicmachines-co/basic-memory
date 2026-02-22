"""Tests for the SchemaClient typed API client.

Covers __init__, validate(), infer(), and diff() methods.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response, Request

from basic_memory.mcp.clients.schema import SchemaClient
from basic_memory.schemas.schema import ValidationReport, InferenceReport, DriftReport


@pytest_asyncio.fixture
async def http_client():
    """Provide a real AsyncClient (unused transport — we mock responses)."""
    async with AsyncClient(base_url="http://test") as client:
        yield client


@pytest.fixture
def schema_client(http_client):
    """Create a SchemaClient with a test project id."""
    return SchemaClient(http_client, "test-project-id")


class TestSchemaClientInit:
    """Tests for SchemaClient.__init__."""

    def test_stores_http_client(self, http_client):
        client = SchemaClient(http_client, "proj-123")
        assert client.http_client is http_client

    def test_stores_project_id(self, http_client):
        client = SchemaClient(http_client, "proj-123")
        assert client.project_id == "proj-123"

    def test_builds_base_path(self, http_client):
        client = SchemaClient(http_client, "proj-123")
        assert client._base_path == "/v2/projects/proj-123/schema"


class TestSchemaClientValidate:
    """Tests for SchemaClient.validate()."""

    @pytest.mark.asyncio
    async def test_validate_no_params(self, schema_client, monkeypatch):
        """Validate with no note_type or identifier sends empty params."""
        report_data = {
            "note_type": None,
            "total_notes": 0,
            "valid_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "results": [],
        }

        request = Request("POST", "http://test/v2/projects/test-project-id/schema/validate")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_post(client, url, **kwargs):
            assert url == "/v2/projects/test-project-id/schema/validate"
            assert kwargs.get("params") == {}
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_post", mock_call_post)

        result = await schema_client.validate()
        assert isinstance(result, ValidationReport)
        assert result.total_notes == 0

    @pytest.mark.asyncio
    async def test_validate_with_note_type(self, schema_client, monkeypatch):
        """Validate sends note_type as query param."""
        report_data = {
            "note_type": "person",
            "total_notes": 5,
            "valid_count": 4,
            "warning_count": 1,
            "error_count": 0,
            "results": [],
        }

        request = Request("POST", "http://test/v2/projects/test-project-id/schema/validate")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_post(client, url, **kwargs):
            assert kwargs["params"]["note_type"] == "person"
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_post", mock_call_post)

        result = await schema_client.validate(note_type="person")
        assert result.note_type == "person"
        assert result.total_notes == 5

    @pytest.mark.asyncio
    async def test_validate_with_identifier(self, schema_client, monkeypatch):
        """Validate sends identifier as query param."""
        report_data = {
            "note_type": None,
            "total_notes": 1,
            "valid_count": 1,
            "warning_count": 0,
            "error_count": 0,
            "results": [],
        }

        request = Request("POST", "http://test/v2/projects/test-project-id/schema/validate")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_post(client, url, **kwargs):
            assert kwargs["params"]["identifier"] == "people/alice"
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_post", mock_call_post)

        result = await schema_client.validate(identifier="people/alice")
        assert result.total_notes == 1


class TestSchemaClientInfer:
    """Tests for SchemaClient.infer()."""

    @pytest.mark.asyncio
    async def test_infer_default_threshold(self, schema_client, monkeypatch):
        """Infer sends note_type and default threshold."""
        report_data = {
            "note_type": "person",
            "notes_analyzed": 10,
            "field_frequencies": [],
            "suggested_schema": {},
            "suggested_required": ["name"],
            "suggested_optional": ["email"],
            "excluded": [],
        }

        request = Request("POST", "http://test/v2/projects/test-project-id/schema/infer")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_post(client, url, **kwargs):
            assert url == "/v2/projects/test-project-id/schema/infer"
            assert kwargs["params"]["note_type"] == "person"
            assert kwargs["params"]["threshold"] == 0.25
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_post", mock_call_post)

        result = await schema_client.infer("person")
        assert isinstance(result, InferenceReport)
        assert result.notes_analyzed == 10
        assert result.suggested_required == ["name"]

    @pytest.mark.asyncio
    async def test_infer_custom_threshold(self, schema_client, monkeypatch):
        """Infer passes custom threshold."""
        report_data = {
            "note_type": "meeting",
            "notes_analyzed": 5,
            "field_frequencies": [],
            "suggested_schema": {},
            "suggested_required": [],
            "suggested_optional": [],
            "excluded": [],
        }

        request = Request("POST", "http://test/v2/projects/test-project-id/schema/infer")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_post(client, url, **kwargs):
            assert kwargs["params"]["threshold"] == 0.5
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_post", mock_call_post)

        result = await schema_client.infer("meeting", threshold=0.5)
        assert result.note_type == "meeting"


class TestSchemaClientDiff:
    """Tests for SchemaClient.diff()."""

    @pytest.mark.asyncio
    async def test_diff(self, schema_client, monkeypatch):
        """Diff calls GET with note_type in path."""
        report_data = {
            "note_type": "person",
            "new_fields": [],
            "dropped_fields": [],
            "cardinality_changes": [],
        }

        request = Request("GET", "http://test/v2/projects/test-project-id/schema/diff/person")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_get(client, url, **kwargs):
            assert url == "/v2/projects/test-project-id/schema/diff/person"
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_get", mock_call_get)

        result = await schema_client.diff("person")
        assert isinstance(result, DriftReport)
        assert result.note_type == "person"

    @pytest.mark.asyncio
    async def test_diff_with_drift(self, schema_client, monkeypatch):
        """Diff returns populated drift report."""
        report_data = {
            "note_type": "person",
            "new_fields": [
                {
                    "name": "role",
                    "source": "observation",
                    "count": 8,
                    "total": 10,
                    "percentage": 80.0,
                }
            ],
            "dropped_fields": [
                {
                    "name": "email",
                    "source": "observation",
                    "count": 1,
                    "total": 10,
                    "percentage": 10.0,
                }
            ],
            "cardinality_changes": ["skills: single -> array"],
        }

        request = Request("GET", "http://test/v2/projects/test-project-id/schema/diff/person")
        mock_response = Response(200, json=report_data, request=request)

        async def mock_call_get(client, url, **kwargs):
            return mock_response

        monkeypatch.setattr("basic_memory.mcp.clients.schema.call_get", mock_call_get)

        result = await schema_client.diff("person")
        assert len(result.new_fields) == 1
        assert result.new_fields[0].name == "role"
        assert len(result.dropped_fields) == 1
        assert result.cardinality_changes == ["skills: single -> array"]
