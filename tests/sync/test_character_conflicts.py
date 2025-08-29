"""Test character-related sync conflicts and permalink generation."""

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy.exc import IntegrityError

from basic_memory.config import ProjectConfig
from basic_memory.repository import EntityRepository
from basic_memory.sync.sync_service import SyncService
from basic_memory.utils import (
    generate_permalink,
    normalize_file_path_for_comparison,
    detect_potential_file_conflicts,
)


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestUtilityFunctions:
    """Test utility functions for file path normalization and conflict detection."""

    def test_normalize_file_path_for_comparison(self):
        """Test file path normalization for conflict detection."""
        # Case sensitivity normalization
        assert (
            normalize_file_path_for_comparison("Finance/Investment.md") == "finance/investment.md"
        )
        assert (
            normalize_file_path_for_comparison("FINANCE/INVESTMENT.MD") == "finance/investment.md"
        )

        # Path separator normalization
        assert (
            normalize_file_path_for_comparison("Finance\\Investment.md") == "finance/investment.md"
        )

        # Multiple slash handling
        assert (
            normalize_file_path_for_comparison("Finance//Investment.md") == "finance/investment.md"
        )

    def test_detect_potential_file_conflicts(self):
        """Test the enhanced conflict detection function."""
        existing_paths = [
            "Finance/Investment.md",
            "finance/Investment.md",
            "docs/my-feature.md",
            "docs/my feature.md",
        ]

        # Case sensitivity conflict
        conflicts = detect_potential_file_conflicts("FINANCE/INVESTMENT.md", existing_paths)
        assert "Finance/Investment.md" in conflicts
        assert "finance/Investment.md" in conflicts

        # Permalink conflict (space vs hyphen)
        conflicts = detect_potential_file_conflicts("docs/my_feature.md", existing_paths)
        assert "docs/my-feature.md" in conflicts
        assert "docs/my feature.md" in conflicts


class TestPermalinkGeneration:
    """Test permalink generation with various character scenarios."""

    def test_hyphen_handling(self):
        """Test that hyphens in filenames are handled consistently."""
        # File with existing hyphens
        assert generate_permalink("docs/my-feature.md") == "docs/my-feature"
        assert generate_permalink("docs/basic-memory bug.md") == "docs/basic-memory-bug"

        # File with spaces that become hyphens
        assert generate_permalink("docs/my feature.md") == "docs/my-feature"

        # Mixed scenarios
        assert generate_permalink("docs/my-old feature.md") == "docs/my-old-feature"

    def test_forward_slash_handling(self):
        """Test that forward slashes are handled properly."""
        # Normal directory structure
        assert generate_permalink("Finance/Investment.md") == "finance/investment"

        # Path with spaces in directory names
        assert generate_permalink("My Finance/Investment.md") == "my-finance/investment"

    def test_case_sensitivity_normalization(self):
        """Test that case differences are normalized consistently."""
        # Same logical path with different cases
        assert generate_permalink("Finance/Investment.md") == "finance/investment"
        assert generate_permalink("finance/Investment.md") == "finance/investment"
        assert generate_permalink("FINANCE/INVESTMENT.md") == "finance/investment"

    def test_unicode_character_handling(self):
        """Test that international characters are handled properly."""
        # Italian characters as mentioned in user feedback
        assert (
            generate_permalink("Finance/Punti Chiave di Peter Lynch.md")
            == "finance/punti-chiave-di-peter-lynch"
        )

        # Chinese characters (should be preserved)
        assert generate_permalink("中文/测试文档.md") == "中文/测试文档"

        # Mixed international characters
        assert generate_permalink("docs/Café München.md") == "docs/cafe-munchen"

    def test_special_punctuation(self):
        """Test handling of special punctuation characters."""
        # Apostrophes should be removed
        assert generate_permalink("Peter's Guide.md") == "peters-guide"

        # Other punctuation should become hyphens
        assert generate_permalink("Q&A Session.md") == "q-a-session"


@pytest.mark.asyncio
class TestSyncConflictHandling:
    """Test sync service handling of file path and permalink conflicts."""

    async def test_file_path_conflict_detection(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
        entity_repository: EntityRepository,
    ):
        """Test that file path conflicts are detected during move operations."""
        project_dir = project_config.home

        # Create two files
        content1 = dedent("""
        ---
        type: knowledge
        ---
        # Document One
        This is the first document.
        """)

        content2 = dedent("""
        ---
        type: knowledge
        ---
        # Document Two  
        This is the second document.
        """)

        await create_test_file(project_dir / "doc1.md", content1)
        await create_test_file(project_dir / "doc2.md", content2)

        # Initial sync
        await sync_service.sync(project_config.home)

        # Verify both entities exist
        entities = await entity_repository.find_all()
        assert len(entities) == 2

        # Now simulate a move where doc1.md tries to move to doc2.md's location
        # This should be handled gracefully, not throw an IntegrityError

        # First, get the entities
        entity1 = await entity_repository.get_by_file_path("doc1.md")
        entity2 = await entity_repository.get_by_file_path("doc2.md")

        assert entity1 is not None
        assert entity2 is not None

        # Simulate the conflict scenario
        with pytest.raises(Exception) as exc_info:
            # This should detect the conflict and handle it gracefully
            await sync_service.handle_move("doc1.md", "doc2.md")

        # The exception should be a meaningful error, not an IntegrityError
        assert not isinstance(exc_info.value, IntegrityError)

    async def test_hyphen_filename_conflict(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
        entity_repository: EntityRepository,
    ):
        """Test conflict when filename with hyphens conflicts with generated permalink."""
        project_dir = project_config.home

        # Create file with spaces (will generate permalink with hyphens)
        content1 = dedent("""
        ---
        type: knowledge  
        ---
        # Basic Memory Bug
        This file has spaces in the name.
        """)

        # Create file with hyphens (already has hyphens in filename)
        content2 = dedent("""
        ---
        type: knowledge
        ---
        # Basic Memory Bug Report
        This file has hyphens in the name.
        """)

        await create_test_file(project_dir / "basic memory bug.md", content1)
        await create_test_file(project_dir / "basic-memory-bug.md", content2)

        # Sync should handle this without conflict
        await sync_service.sync(project_config.home)

        # Verify both entities were created with unique permalinks
        entities = await entity_repository.find_all()
        assert len(entities) == 2

        # Check that permalinks are unique
        permalinks = [entity.permalink for entity in entities if entity.permalink]
        assert len(set(permalinks)) == len(permalinks), "Permalinks should be unique"

    async def test_case_sensitivity_conflict(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
        entity_repository: EntityRepository,
    ):
        """Test conflict handling when case differences cause issues."""
        import platform

        project_dir = project_config.home

        # Create directory structure that might cause case conflicts
        (project_dir / "Finance").mkdir(parents=True, exist_ok=True)
        (project_dir / "finance").mkdir(parents=True, exist_ok=True)

        content1 = dedent("""
        ---
        type: knowledge
        ---
        # Investment Guide
        Upper case directory.
        """)

        content2 = dedent("""
        ---
        type: knowledge
        ---
        # Investment Tips
        Lower case directory.
        """)

        await create_test_file(project_dir / "Finance" / "investment.md", content1)
        await create_test_file(project_dir / "finance" / "investment.md", content2)

        # Sync should handle case differences properly
        await sync_service.sync(project_config.home)

        # Verify entities were created
        entities = await entity_repository.find_all()

        # On case-insensitive file systems (macOS, Windows), only one entity will be created
        # On case-sensitive file systems (Linux), two entities will be created
        if platform.system() in ["Darwin", "Windows"]:
            # Case-insensitive file systems
            assert len(entities) >= 1
            # Only one of the paths will exist
            file_paths = [entity.file_path for entity in entities]
            assert any(
                path in ["Finance/investment.md", "finance/investment.md"] for path in file_paths
            )
        else:
            # Case-sensitive file systems (Linux)
            assert len(entities) >= 2
            # Check that file paths are preserved correctly
            file_paths = [entity.file_path for entity in entities]
            assert "Finance/investment.md" in file_paths
            assert "finance/investment.md" in file_paths

    async def test_move_conflict_resolution(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
        entity_repository: EntityRepository,
    ):
        """Test that move conflicts are resolved with proper error handling."""
        project_dir = project_config.home

        # Create three files in a scenario that could cause move conflicts
        await create_test_file(project_dir / "file-a.md", "# File A")
        await create_test_file(project_dir / "file-b.md", "# File B")
        await create_test_file(project_dir / "temp.md", "# Temp File")

        # Initial sync
        await sync_service.sync(project_config.home)

        # Simulate a complex move scenario where files swap locations
        # This is the kind of scenario that caused the original bug

        # Get the entities
        entity_a = await entity_repository.get_by_file_path("file-a.md")
        entity_b = await entity_repository.get_by_file_path("file-b.md")
        entity_temp = await entity_repository.get_by_file_path("temp.md")

        assert all([entity_a, entity_b, entity_temp])

        # Try to move file-a to file-b's location (should detect conflict)
        try:
            await sync_service.handle_move("file-a.md", "file-b.md")
            # If this doesn't raise an exception, the conflict was resolved

            # Verify the state is consistent
            updated_entities = await entity_repository.find_all()
            file_paths = [entity.file_path for entity in updated_entities]

            # Should not have duplicate file paths
            assert len(file_paths) == len(set(file_paths)), "File paths should be unique"

        except Exception as e:
            # If an exception is raised, it should be a meaningful error
            assert "conflict" in str(e).lower() or "already exists" in str(e).lower()
            assert not isinstance(e, IntegrityError), "Should not be a raw IntegrityError"


@pytest.mark.asyncio
class TestEnhancedErrorMessages:
    """Test that error messages provide helpful guidance for character conflicts."""

    async def test_helpful_error_for_hyphen_conflict(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
    ):
        """Test that hyphen conflicts generate helpful error messages."""
        # This test will be implemented after we enhance the error handling
        pass

    async def test_helpful_error_for_case_conflict(
        self,
        sync_service: SyncService,
        project_config: ProjectConfig,
    ):
        """Test that case sensitivity conflicts generate helpful error messages."""
        # This test will be implemented after we enhance the error handling
        pass
