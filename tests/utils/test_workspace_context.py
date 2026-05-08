"""Tests for request-local workspace permalink context."""

import pytest

from basic_memory.workspace_context import (
    WORKSPACE_SLUG_HEADER,
    WORKSPACE_TYPE_HEADER,
    current_workspace_permalink_context,
    workspace_permalink_context,
    workspace_permalink_headers,
    workspace_slug_for_canonical_permalinks,
)


def test_workspace_permalink_context_requires_slug_and_type_together():
    with pytest.raises(ValueError, match="provided together"):
        with workspace_permalink_context("team-paul", None):
            pass


def test_workspace_permalink_context_rejects_unsafe_slug():
    with pytest.raises(ValueError, match=WORKSPACE_SLUG_HEADER):
        with workspace_permalink_context("../team-paul", "organization"):
            pass


def test_workspace_permalink_context_rejects_unknown_type():
    with pytest.raises(ValueError, match=WORKSPACE_TYPE_HEADER):
        with workspace_permalink_context("team-paul", "enterprise"):
            pass


def test_workspace_permalink_headers_reflect_active_context():
    assert workspace_permalink_headers() == {}

    with workspace_permalink_context("team-paul", "organization"):
        context = current_workspace_permalink_context()
        assert context is not None
        assert context.workspace_slug == "team-paul"
        assert workspace_permalink_headers() == {
            WORKSPACE_SLUG_HEADER: "team-paul",
            WORKSPACE_TYPE_HEADER: "organization",
        }

    assert current_workspace_permalink_context() is None


def test_personal_workspace_slug_is_canonical_permalink_prefix():
    with workspace_permalink_context("personal", "personal"):
        assert workspace_slug_for_canonical_permalinks() == "personal"
