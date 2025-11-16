"""Comprehensive test suite for kebab_filenames configuration.

Tests the BASIC_MEMORY_KEBAB_FILENAMES configuration option which controls
whether note filenames are converted to kebab-case (lowercase with hyphens).

Feature added in PR #260 to handle forward slashes in filenames.
This test suite was expanded to comprehensively test all kebab-case transformations.

Key behaviors tested:
1. When kebab_filenames=true: All special characters, spaces, periods, underscores,
   and mixed case are converted to lowercase kebab-case
2. When kebab_filenames=false: Original formatting is preserved (backward compatibility)
3. Folder paths are not affected by kebab_filenames setting
4. Permalinks are always kebab-case regardless of kebab_filenames setting
"""

import pytest
from basic_memory.mcp.tools import write_note
from basic_memory.config import ConfigManager


# =============================================================================
# Basic Transformations (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_spaces_to_hyphens(app, test_project, app_config):
    """Test that spaces are converted to hyphens when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="My Awesome Note",
        folder="test",
        content="Testing space conversion",
    )

    assert "file_path: test/my-awesome-note.md" in result
    assert "permalink: test/my-awesome-note" in result


@pytest.mark.asyncio
async def test_write_note_underscores_to_hyphens(app, test_project, app_config):
    """Test that underscores are converted to hyphens when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="my_note_with_underscores",
        folder="test",
        content="Testing underscore conversion",
    )

    assert "file_path: test/my-note-with-underscores.md" in result
    assert "permalink: test/my-note-with-underscores" in result


@pytest.mark.asyncio
async def test_write_note_camelcase_to_kebab(app, test_project, app_config):
    """Test that CamelCase is converted to kebab-case when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="MyAwesomeFeature",
        folder="test",
        content="Testing CamelCase conversion",
    )

    assert "file_path: test/my-awesome-feature.md" in result
    assert "permalink: test/my-awesome-feature" in result


@pytest.mark.asyncio
async def test_write_note_mixed_case_to_lowercase(app, test_project, app_config):
    """Test that mixed case is converted to lowercase when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="MIXED_Case_Example",
        folder="test",
        content="Testing case conversion",
    )

    assert "file_path: test/mixed-case-example.md" in result
    assert "permalink: test/mixed-case-example" in result


# =============================================================================
# Period Handling (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_single_period_preserved(app, test_project, app_config):
    """Test that periods in version numbers are preserved when kebab_filenames=true.

    This preserves semantic meaning of version numbers like "3.0" while still
    converting spaces to hyphens. Only actual file extensions are split off.
    """
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Test 3.0 Version",
        folder="test",
        content="Testing period preservation",
    )

    assert "file_path: test/test-3.0-version.md" in result
    assert "permalink: test/test-3.0-version" in result


@pytest.mark.asyncio
async def test_write_note_multiple_periods_preserved(app, test_project, app_config):
    """Test that multiple periods in version numbers are preserved when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Version 1.2.3 Release",
        folder="test",
        content="Testing multiple period preservation",
    )

    assert "file_path: test/version-1.2.3-release.md" in result
    assert "permalink: test/version-1.2.3-release" in result


# =============================================================================
# Special Characters (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_special_chars_to_hyphens(app, test_project, app_config):
    """Test that special characters are converted while preserving periods in version numbers."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Test 2.0: New Feature",
        folder="test",
        content="Testing special character conversion",
    )

    assert "file_path: test/test-2.0-new-feature.md" in result
    assert "permalink: test/test-2.0-new-feature" in result


@pytest.mark.asyncio
async def test_write_note_parentheses_removed(app, test_project, app_config):
    """Test that parentheses are handled while preserving periods in version numbers."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Feature (v2.0) Update",
        folder="test",
        content="Testing parentheses handling",
    )

    assert "file_path: test/feature-v2.0-update.md" in result
    assert "permalink: test/feature-v2.0-update" in result


@pytest.mark.asyncio
async def test_write_note_apostrophes_removed(app, test_project, app_config):
    """Test that apostrophes are removed when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="User's Guide",
        folder="test",
        content="Testing apostrophe handling",
    )

    assert "file_path: test/users-guide.md" in result
    assert "permalink: test/users-guide" in result


# =============================================================================
# Combined Transformations (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_all_transformations_combined(app, test_project, app_config):
    """Test multiple transformation types combined while preserving periods in version numbers."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="MyProject_v3.0: Feature Update (DRAFT)",
        folder="test",
        content="Testing combined transformations",
    )

    assert "file_path: test/my-project-v3.0-feature-update-draft.md" in result
    assert "permalink: test/my-project-v3.0-feature-update-draft" in result


@pytest.mark.asyncio
async def test_write_note_consecutive_special_chars_collapsed(app, test_project, app_config):
    """Test that consecutive special characters collapse to single hyphen."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Test___Multiple---Separators",
        folder="test",
        content="Testing consecutive special character collapse",
    )

    # Multiple underscores/hyphens should collapse to single hyphen
    assert "file_path: test/test-multiple-separators.md" in result
    assert "permalink: test/test-multiple-separators" in result


# =============================================================================
# Edge Cases (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_leading_trailing_hyphens_trimmed(app, test_project, app_config):
    """Test that leading/trailing hyphens are trimmed when kebab_filenames=true."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="---Test Note---",
        folder="test",
        content="Testing leading/trailing hyphen trimming",
    )

    assert "file_path: test/test-note.md" in result
    assert "permalink: test/test-note" in result


@pytest.mark.asyncio
async def test_write_note_all_special_chars_becomes_valid_filename(app, test_project, app_config):
    """Test that a title with mostly special characters becomes valid."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="!!!Test!!!",
        folder="test",
        content="Testing all special characters",
    )

    assert "file_path: test/test.md" in result
    assert "permalink: test/test" in result


# =============================================================================
# Folder Path Handling (kebab_filenames=true)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_folder_path_unaffected(app, test_project, app_config):
    """Test that folder paths are NOT affected by kebab_filenames setting."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="My_Folder/Sub Folder",  # Folder should remain as-is
        content="Testing folder path preservation",
    )

    # Folder paths should be preserved (sanitized but not kebab-cased)
    assert "file_path: My_Folder/Sub Folder/test-note.md" in result
    assert "permalink: my-folder/sub-folder/test-note" in result


@pytest.mark.asyncio
async def test_write_note_root_folder_with_kebab(app, test_project, app_config):
    """Test kebab_filenames preserves periods in version numbers with root folder."""
    ConfigManager().config.kebab_filenames = True

    result = await write_note.fn(
        project=test_project.name,
        title="Test 3.0 Note",
        folder="",  # Root folder
        content="Testing root folder",
    )

    assert "file_path: test-3.0-note.md" in result
    assert "permalink: test-3.0-note" in result


# =============================================================================
# Backward Compatibility (kebab_filenames=false)
# =============================================================================


@pytest.mark.asyncio
async def test_write_note_kebab_disabled_preserves_original(app, test_project, app_config):
    """Test that original formatting is preserved when kebab_filenames=false."""
    ConfigManager().config.kebab_filenames = False

    result = await write_note.fn(
        project=test_project.name,
        title="Test 3.0 Version",
        folder="test",
        content="Testing backward compatibility",
    )

    # Periods and spaces should be preserved
    assert "file_path: test/Test 3.0 Version.md" in result
    # Permalinks are ALWAYS kebab-case regardless of setting, and preserve periods
    assert "permalink: test/test-3.0-version" in result


@pytest.mark.asyncio
async def test_write_note_kebab_disabled_preserves_underscores(app, test_project, app_config):
    """Test that underscores are preserved when kebab_filenames=false."""
    ConfigManager().config.kebab_filenames = False

    result = await write_note.fn(
        project=test_project.name,
        title="my_note_example",
        folder="test",
        content="Testing underscore preservation",
    )

    assert "file_path: test/my_note_example.md" in result
    assert "permalink: test/my-note-example" in result


@pytest.mark.asyncio
async def test_write_note_kebab_disabled_preserves_case(app, test_project, app_config):
    """Test that case is preserved when kebab_filenames=false."""
    ConfigManager().config.kebab_filenames = False

    result = await write_note.fn(
        project=test_project.name,
        title="MyAwesomeNote",
        folder="test",
        content="Testing case preservation",
    )

    assert "file_path: test/MyAwesomeNote.md" in result
    assert "permalink: test/my-awesome-note" in result


# =============================================================================
# Permalink Consistency (both modes)
# =============================================================================


@pytest.mark.asyncio
async def test_permalinks_always_kebab_case(app, test_project, app_config):
    """Test that permalinks are ALWAYS kebab-case regardless of kebab_filenames setting.

    This is important: even when kebab_filenames=false (preserving filename formatting),
    permalinks should still be kebab-case for URL consistency. Both modes preserve periods
    in version numbers.
    """
    # Test with kebab disabled
    ConfigManager().config.kebab_filenames = False

    result1 = await write_note.fn(
        project=test_project.name,
        title="Test Note 1",
        folder="test",
        content="Testing permalink consistency",
    )

    # Filename preserves original, permalink is kebab-case
    assert "file_path: test/Test Note 1.md" in result1
    assert "permalink: test/test-note-1" in result1

    # Test with kebab enabled
    ConfigManager().config.kebab_filenames = True

    result2 = await write_note.fn(
        project=test_project.name,
        title="Test Note 2",
        folder="test",
        content="Testing permalink consistency",
    )

    # Both filename and permalink are kebab-case
    assert "file_path: test/test-note-2.md" in result2
    assert "permalink: test/test-note-2" in result2
