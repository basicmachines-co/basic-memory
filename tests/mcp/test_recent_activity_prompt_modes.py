"""Tests for recent_activity_prompt with different modes.

The prompt delegates to the recent_activity tool which returns a formatted string.
These tests verify the prompt correctly uses the tool output and adds guidance.
"""

import pytest
from typing import Any, cast

from basic_memory.mcp.prompts.recent_activity import recent_activity_prompt


@pytest.mark.asyncio
async def test_recent_activity_prompt_resolves_default_scope(monkeypatch, config_manager):
    """An omitted prompt scope resolves the default before dispatching the tool."""
    default_project = config_manager.config.default_project
    tool_output = f"""## Recent Activity: {default_project} (7d)

- API Design Decision (2 hours ago)
"""

    async def fake_fn(**_kwargs):
        return tool_output

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.bm_project", fake_fn)

    out = await recent_activity_prompt(timeframe="7d", project=None)  # pyright: ignore[reportGeneralTypeIssues]

    # Should contain the tool output
    assert f"Recent Activity: {default_project} (7d)" in out
    assert "API Design Decision" in out

    # Should contain prompt guidance
    assert "Recent Activity Context" in out
    assert "Next Steps" in out
    assert "Capture Opportunity" in out
    # Guidance carries the resolved route rather than a made-up project named default.
    assert 'project="default"' not in out
    assert "all projects" not in out
    assert "default project" not in out
    assert f"showing recent activity from project '{default_project}'." in out
    assert f"project='{default_project}'" in out


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

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.bm_project", fake_fn)

    out = await recent_activity_prompt(timeframe="1d", project="my-project")  # pyright: ignore[reportGeneralTypeIssues]

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

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.bm_project", fake_fn)

    await recent_activity_prompt(timeframe="2d", project="test-proj")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["since"] == "2d"
    assert captured_kwargs["project"] == "test-proj"
    assert "type" not in captured_kwargs


@pytest.mark.asyncio
async def test_recent_activity_prompt_defaults_timeframe(monkeypatch, config_manager):
    """Prompt should fall back to 7d when timeframe omitted or falsy."""
    captured_kwargs = {}

    async def fake_fn(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity"

    monkeypatch.setattr("basic_memory.mcp.prompts.recent_activity.bm_project", fake_fn)

    await recent_activity_prompt(timeframe=cast(Any, None), project=None)

    assert captured_kwargs["since"] == "7d"
    assert captured_kwargs["project"] == config_manager.config.default_project
