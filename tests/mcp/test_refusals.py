"""refusal() and api_refusal(): the bm_* error shapes (SPEC-35.2 C11)."""

import json
from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.refusals import api_refusal, refusal


def _client_error(status_code: int, *, json: Any = None, text: str | None = None) -> ToolError:
    """A ToolError raised from an HTTP answer, the way the typed clients raise it."""
    request = httpx.Request("PATCH", "http://test/entities/1")
    answer = httpx.Response(status_code, request=request, json=json, text=text)
    error = ToolError("request failed")
    error.__cause__ = httpx.HTTPStatusError("failed", request=request, response=answer)
    return error


def test_refusal_text_and_json_forms():
    assert str(refusal("NOT_FOUND", "gone")) == "NOT_FOUND: gone"
    assert str(refusal("NOT_FOUND", "gone", try_instead="bm_search")) == (
        "NOT_FOUND: gone\nTry instead: bm_search"
    )
    assert json.loads(str(refusal("LOCKED", "busy", output_format="json"))) == {
        "error": "LOCKED",
        "message": "busy",
    }


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(400, "INVALID_ARGUMENTS"), (404, "NOT_FOUND"), (415, "UNSUPPORTED"), (423, "LOCKED")],
)
def test_api_refusal_maps_status_codes(status_code, code):
    refused = api_refusal(_client_error(status_code, json={"detail": "said no"}))

    assert refused is not None
    assert (refused.code, refused.message, refused.revision_conflict) == (code, "said no", False)


def test_api_refusal_reads_a_revision_conflict():
    refused = api_refusal(
        _client_error(409, json={"detail": {"message": "changed", "db_checksum": "abc"}})
    )

    assert refused is not None
    assert (refused.code, refused.message) == ("REVISION_CONFLICT", "changed")
    assert (refused.revision_conflict, refused.current_checksum) == (True, "abc")


def test_api_refusal_without_a_json_body_keeps_the_client_message():
    refused = api_refusal(_client_error(400, text="<html>bad gateway</html>"))

    assert refused is not None
    assert refused.message == "request failed"


@pytest.mark.parametrize("status_code", [500, 401])
def test_unmapped_statuses_are_not_refusals(status_code):
    assert api_refusal(_client_error(status_code, json={"detail": "x"})) is None


def test_transport_failures_are_not_refusals():
    error = ToolError("no answer")
    error.__cause__ = httpx.ConnectError("down")

    assert api_refusal(error) is None
