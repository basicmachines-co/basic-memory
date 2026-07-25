from basic_memory.mcp.prompts.ai_assistant_guide import ai_assistant_guide


def test_ai_assistant_guide_exists():
    """The bundled guide should orient agents and teach progressive docs discovery."""
    guide = ai_assistant_guide()

    assert "# AI Assistant Guide" in guide
    assert "recent_activity" in guide
    assert "project_id" in guide
    assert "directory" in guide
    assert "https://docs.basicmemory.com/llms.txt" in guide
    assert "https://docs.basicmemory.com/raw/...md" in guide
    assert "https://docs.basicmemory.com/llms-full.txt" in guide
    assert "input schemas exposed by this MCP server are authoritative" in guide
