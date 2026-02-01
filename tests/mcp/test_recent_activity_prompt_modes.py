"""Tests for recent_activity_prompt with different modes.

The prompt delegates to the recent_activity tool which returns a formatted string.
These tests verify the prompt correctly uses the tool output and adds guidance.
"""

import pytest

from basic_memory.mcp.prompts.recent_activity import recent_activity_prompt


@pytest.mark.asyncio
async def test_recent_activity_prompt_discovery_mode(monkeypatch):
    """Test prompt in discovery mode (no project specified)."""
    # The tool returns a formatted string in discovery mode
    tool_output = """## Recent Activity Summary (7d)

**Most Active Project:** project-alpha (5 items)
- 🔧 **Latest:** API Design Decision (2 hours ago)
- 📋 **Focus areas:** decisions, specs

**Other Active Projects:**
- **project-beta** (3 items)

**Summary:** 2 active projects, 8 recent items
"""

    async def fake_fn(**_kwargs):
        return tool_output

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    out = await recent_activity_prompt.fn(timeframe="7d", project=None)  # pyright: ignore[reportGeneralTypeIssues]

    # Should contain the tool output
    assert "Recent Activity Summary (7d)" in out
    assert "Most Active Project" in out

    # Should contain prompt guidance
    assert "Recent Activity Context" in out
    assert "Next Steps" in out
    assert "Capture Opportunity" in out


@pytest.mark.asyncio
async def test_recent_activity_prompt_project_mode(monkeypatch):
    """Test prompt in project-specific mode."""
    # The tool returns a formatted string for a specific project
    tool_output = """## Recent Activity: my-project (1d)

**📄 Recent Notes & Documents (3):**
  • API Design Decision
  • User Authentication Spec
  • Database Schema

**Activity Summary:** 3 items found
"""

    async def fake_fn(**_kwargs):
        return tool_output

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    out = await recent_activity_prompt.fn(timeframe="1d", project="my-project")  # pyright: ignore[reportGeneralTypeIssues]

    # Should contain the tool output
    assert "Recent Activity: my-project" in out
    assert "Recent Notes & Documents" in out

    # Should contain prompt guidance
    assert "Recent Activity Context" in out
    assert "my-project" in out  # Project mentioned in guidance
    assert "Next Steps" in out


@pytest.mark.asyncio
async def test_recent_activity_prompt_passes_correct_params(monkeypatch):
    """Test that prompt passes correct parameters to the tool."""
    captured_kwargs = {}

    async def fake_fn(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity"

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    await recent_activity_prompt.fn(timeframe="2d", project="test-proj")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["timeframe"] == "2d"
    assert captured_kwargs["project"] == "test-proj"
    assert "type" not in captured_kwargs


@pytest.mark.asyncio
async def test_recent_activity_prompt_defaults_timeframe(monkeypatch):
    """Prompt should fall back to 7d when timeframe omitted or falsy."""
    captured_kwargs = {}

    async def fake_fn(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity"

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.recent_activity.fn", fake_fn)

    await recent_activity_prompt.fn(timeframe=None, project=None)  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["timeframe"] == "7d"
    assert captured_kwargs["project"] is None
