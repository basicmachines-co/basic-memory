"""Test that imported conversations are properly indexed with correct permalink and title.

This test verifies issue #452 - Imported conversations not indexed correctly.
"""

import pytest

from basic_memory.config import ProjectConfig
from basic_memory.importers.claude_conversations_importer import ClaudeConversationsImporter
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository import EntityRepository
from basic_memory.services import EntityService
from basic_memory.services.search_service import SearchService
from basic_memory.schemas.search import SearchQuery
from basic_memory.sync.sync_service import SyncService


@pytest.mark.asyncio
async def test_imported_conversations_have_correct_permalink_and_title(
    project_config: ProjectConfig,
    sync_service: SyncService,
    entity_service: EntityService,
    entity_repository: EntityRepository,
    search_service: SearchService,
):
    """Test that imported conversations have correct permalink and title after sync.

    Issue #452: Imported conversations show permalink: null in search results
    and title shows as filename instead of frontmatter title.
    """
    base_path = project_config.home

    # Create parser and processor for importer
    parser = EntityParser(base_path)
    processor = MarkdownProcessor(parser)

    # Create importer
    importer = ClaudeConversationsImporter(base_path, processor)

    # Sample conversation data
    conversations = [
        {
            "uuid": "test-123",
            "name": "My Test Conversation Title",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T11:00:00Z",
            "chat_messages": [
                {
                    "uuid": "msg-1",
                    "sender": "human",
                    "created_at": "2025-01-15T10:00:00Z",
                    "text": "Hello world",
                    "content": [{"type": "text", "text": "Hello world"}],
                    "attachments": [],
                },
                {
                    "uuid": "msg-2",
                    "sender": "assistant",
                    "created_at": "2025-01-15T10:01:00Z",
                    "text": "Hello!",
                    "content": [{"type": "text", "text": "Hello!"}],
                    "attachments": [],
                },
            ],
        }
    ]

    # Run import
    result = await importer.import_data(conversations, "conversations")
    assert result.success, f"Import failed: {result}"
    assert result.conversations == 1

    # Verify the file was created with correct content
    conv_path = base_path / "conversations" / "20250115-My_Test_Conversation_Title.md"
    assert conv_path.exists(), f"Expected file at {conv_path}"

    content = conv_path.read_text()
    assert "---" in content, "File should have frontmatter markers"
    assert "title: My Test Conversation Title" in content, "File should have title in frontmatter"
    assert "permalink: conversations/20250115-My_Test_Conversation_Title" in content, (
        "File should have permalink in frontmatter"
    )

    # Run sync to index the imported file
    await sync_service.sync(base_path, project_config.name)

    # Verify entity in database
    entities = await entity_repository.find_all()
    assert len(entities) == 1, f"Expected 1 entity, got {len(entities)}"

    entity = entities[0]

    # These are the key assertions for issue #452
    assert entity.title == "My Test Conversation Title", (
        f"Title should be from frontmatter, got: {entity.title}"
    )
    assert entity.permalink == "conversations/20250115-My_Test_Conversation_Title", (
        f"Permalink should be from frontmatter, got: {entity.permalink}"
    )

    # Verify search index also has correct data
    results = await search_service.search(SearchQuery(text="Test Conversation"))
    assert len(results) >= 1, "Should find the conversation in search"

    # Find our entity in search results
    search_result = next((r for r in results if r.entity_id == entity.id), None)
    assert search_result is not None, "Entity should be in search results"
    assert search_result.title == "My Test Conversation Title", (
        f"Search title should be from frontmatter, got: {search_result.title}"
    )
    assert search_result.permalink == "conversations/20250115-My_Test_Conversation_Title", (
        f"Search permalink should not be null, got: {search_result.permalink}"
    )
