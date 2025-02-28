from basic_memory.mcp.prompts.json_canvas_spec import json_canvas_spec
from basic_memory.mcp.prompts.ai_assistant_guide import ai_assistant_guide


import pytest


@pytest.mark.asyncio
async def test_canvas_spec_resource_exists(app):
    """Test that the canvas spec resource exists and returns content."""
    # Call the resource function
    spec_content = json_canvas_spec()

    # Verify basic characteristics of the content
    assert spec_content is not None
    assert isinstance(spec_content, str)
    assert len(spec_content) > 0

    # Verify it contains expected sections of the Canvas spec
    assert "JSON Canvas Spec" in spec_content
    assert "nodes" in spec_content
    assert "edges" in spec_content


@pytest.mark.asyncio
async def test_ai_assistant_guide_exists(app):
    """Test that the canvas spec resource exists and returns content."""
    # Call the resource function
    guide = ai_assistant_guide()

    # Verify basic characteristics of the content
    assert guide is not None
    assert isinstance(guide, str)
    assert len(guide) > 0

    # Verify it contains expected sections of the Canvas spec
    assert "# AI Assistant Guide" in guide
