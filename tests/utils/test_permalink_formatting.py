"""Test permalink formatting during sync."""

from pathlib import Path

import pytest

from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService
from basic_memory.sync.sync_service import SyncService
from basic_memory.utils import generate_permalink, sanitize_filename


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_permalink_formatting(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that permalinks are properly formatted during sync.

    This ensures:
    - Underscores are converted to hyphens
    - Spaces are converted to hyphens
    - Mixed case is lowercased
    - Directory structure is preserved
    - Multiple directories work correctly
    """
    project_dir = project_config.home

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
        content = """
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
    await sync_service.sync(project_config.home)

    # Verify permalinks
    for filename, expected_permalink in test_cases:
        entity = await entity_service.repository.get_by_file_path(filename)
        assert entity.permalink == expected_permalink, (
            f"File {filename} should have permalink {expected_permalink}"
        )


@pytest.mark.parametrize(
    "input_path, expected",
    [
        ("test/Über File.md", "test/uber-file"),
        ("docs/résumé.md", "docs/resume"),
        ("notes/Déjà vu.md", "notes/deja-vu"),
        ("papers/Jürgen's Findings.md", "papers/jurgens-findings"),
        ("archive/François Müller.md", "archive/francois-muller"),
        ("research/Søren Kierkegård.md", "research/soren-kierkegard"),
        ("articles/El Niño.md", "articles/el-nino"),
    ],
)
def test_latin_accents_transliteration(input_path, expected):
    """Test that Latin letters with accents are properly transliterated."""
    assert generate_permalink(input_path) == expected


@pytest.mark.parametrize(
    "input_path, expected",
    [
        ("中文/测试文档.md", "中文/测试文档"),
        ("notes/北京市.md", "notes/北京市"),
        ("research/上海简介.md", "research/上海简介"),
        ("docs/中文 English Mixed.md", "docs/中文-english-mixed"),
        ("articles/东京Tokyo混合.md", "articles/东京-tokyo-混合"),
        ("papers/汉字_underscore_test.md", "papers/汉字-underscore-test"),
        ("projects/中文CamelCase测试.md", "projects/中文-camel-case-测试"),
    ],
)
def test_chinese_character_preservation(input_path, expected):
    """Test that Chinese characters are preserved in permalinks."""
    assert generate_permalink(input_path) == expected


@pytest.mark.parametrize(
    "input_path, expected",
    [
        ("mixed/北京Café.md", "mixed/北京-cafe"),
        ("notes/东京Tōkyō.md", "notes/东京-tokyo"),
        ("research/München中文.md", "research/munchen-中文"),
        ("docs/Über测试.md", "docs/uber-测试"),
        ("complex/北京Beijing上海Shanghai.md", "complex/北京-beijing-上海-shanghai"),
        ("special/中文!@#$%^&*()_+.md", "special/中文"),
        ("punctuation/你好，世界!.md", "punctuation/你好世界"),
    ],
)
def test_mixed_character_sets(input_path, expected):
    """Test handling of mixed character sets and edge cases."""
    assert generate_permalink(input_path) == expected


@pytest.mark.parametrize(
    "input_title, expected",
    [
        ("Coupon Enable/Disable Feature", "coupon-enable-disable-feature"),
        ("My Awesome Feature", "my-awesome-feature"),
        ("Test_File Name.txt", "test-file-name-txt"),
        ("Feature/With/Multiple/Slashes", "feature-with-multiple-slashes"),
        ("Mixed Case Feature", "mixed-case-feature"),
        ("  Leading and trailing spaces  ", "leading-and-trailing-spaces"),
        ("Special!@#$%^&*()Characters", "special-characters"),
        ("北京/东京", "北京-东京"),
        ("Mixed/中文/English", "mixed-中文-english"),
    ],
)
def test_sanitize_filename(input_title, expected):
    """Test that title sanitization works correctly for filenames."""
    assert sanitize_filename(input_title) == expected


@pytest.mark.asyncio
async def test_entity_file_path_with_kebab_case_config(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that Entity file_path uses kebab-case when configured."""
    # Test with kebab-case enabled (default)
    from basic_memory.schemas.base import Entity
    from basic_memory.config import app_config
    
    # Ensure config is set to kebab-case 
    original_format = app_config.filename_format
    app_config.filename_format = "kebab-case"
    
    try:
        entity = Entity(
            title="Coupon Enable/Disable Feature",
            folder="bugs",
            content="Test content"
        )
        
        # With kebab-case, forward slashes should be converted to hyphens
        expected_file_path = "bugs/coupon-enable-disable-feature.md"
        expected_permalink = "bugs/coupon-enable-disable-feature"
        
        assert entity.file_path == expected_file_path
        assert entity.permalink == expected_permalink
        
        # Test that file_path and permalink are now consistent
        assert entity.permalink == generate_permalink(entity.file_path)
        
    finally:
        # Restore original configuration
        app_config.filename_format = original_format


@pytest.mark.asyncio
async def test_entity_file_path_with_original_config(
    sync_service: SyncService, project_config: ProjectConfig, entity_service: EntityService
):
    """Test that Entity file_path preserves original format when configured."""
    from basic_memory.schemas.base import Entity
    from basic_memory.config import app_config
    
    # Set config to original format
    original_format = app_config.filename_format
    app_config.filename_format = "original"
    
    try:
        entity = Entity(
            title="Coupon Enable/Disable Feature",
            folder="bugs",
            content="Test content"
        )
        
        # With original format, spaces and slashes are preserved
        # This creates the inconsistency that the issue reports
        expected_file_path = "bugs/Coupon Enable/Disable Feature.md"
        expected_permalink = "bugs/coupon-enable-disable-feature"
        
        assert entity.file_path == expected_file_path
        assert entity.permalink == expected_permalink
        
        # This demonstrates the inconsistency when using original format
        assert entity.permalink != generate_permalink(entity.file_path)
        
    finally:
        # Restore original configuration
        app_config.filename_format = original_format
