from basic_memory.mcp.prompts.ai_assistant_guide import ai_assistant_guide


import pytest


@pytest.mark.asyncio
async def test_ai_assistant_guide_exists(app):
    """Test that the AI assistant guide resource exists and returns content."""
    # Call the resource function
    guide = ai_assistant_guide()

    # Verify basic characteristics of the content
    assert guide is not None
    assert isinstance(guide, str)
    assert len(guide) > 0

    assert "# AI Assistant Guide" in guide
