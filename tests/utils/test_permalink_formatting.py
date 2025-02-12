"""Test permalink formatting during sync."""

import pytest
from pathlib import Path

from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService
from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_permalink_formatting(
    sync_service: SyncService, test_config: ProjectConfig, entity_service: EntityService
):
    """Test that permalinks are properly formatted during sync.

    This ensures:
    - Underscores are converted to hyphens
    - Spaces are converted to hyphens
    - Mixed case is lowercased
    - Directory structure is preserved
    - Multiple directories work correctly
    """
    project_dir = test_config.home

    # Test cases with different filename formats
    test_cases = [
        # filename -> expected permalink
        ("my_awesome_feature.md", "my-awesome-feature"),
        ("MIXED_CASE_NAME.md", "mixed-case-name"),
        ("spaces and_underscores.md", "spaces-and-underscores"),
        ("design/model_refactor.md", "design/model-refactor"),
        (
            "test/multiple_word_directory/feature_name.md",
            "test/multiple-word-directory/feature-name",
        ),
    ]

    # Create test files
    for filename, _ in test_cases:
        content = f"""
---
type: knowledge
created: 2024-01-01
modified: 2024-01-01
---
# Test File

Testing permalink generation.
"""
        await create_test_file(project_dir / filename, content)

    # Run sync
    await sync_service.sync(test_config.home)

    # Verify permalinks
    for filename, expected_permalink in test_cases:
        entity = await entity_service.repository.get_by_file_path(filename)
        assert entity.permalink == expected_permalink, (
            f"File {filename} should have permalink {expected_permalink}"
        )
