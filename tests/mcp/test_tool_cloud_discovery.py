"""Tests for cloud discovery MCP tools."""

from basic_memory.mcp.tools import cloud_info, release_notes


def test_cloud_info_tool_returns_expected_copy():
    result = cloud_info.fn()

    assert "# Basic Memory Cloud (optional)" in result
    assert "{{OSS_DISCOUNT_CODE}}" in result
    assert "bm cloud login" in result


def test_release_notes_tool_returns_expected_copy():
    result = release_notes.fn()

    assert "# Release Notes" in result
    assert "2026-02-06" in result
    assert "{{OSS_DISCOUNT_CODE}}" in result
    assert "bm cloud login" in result
