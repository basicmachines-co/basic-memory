"""Tests for local moved-file content planning and post-commit writes."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.file_utils import compute_checksum
from basic_memory.index.local_moves import (
    LocalMoveEntityService,
    LocalProjectIndexMoveContentUpdater,
    merged_frontmatter_markdown,
)
from basic_memory.indexing.project_index_maintenance import ProjectIndexMovedFile
from basic_memory.markdown import EntityMarkdown
from basic_memory.services import FileService


@dataclass(frozen=True, slots=True)
class MovePermalinkConfig:
    """Just the permalink policy flags the move content planner reads."""

    disable_permalinks: bool = False
    update_permalinks_on_move: bool = True


@dataclass(slots=True)
class StaticMoveEntityService:
    """Resolve every permalink to a fixed value for planner tests."""

    app_config: MovePermalinkConfig | None
    permalink: str = "main/archive/renamed"

    async def resolve_permalink(
        self,
        file_path: Path | str,
        markdown: EntityMarkdown | None = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str:
        return self.permalink


def _updater(
    tmp_path: Path,
    entity_service: StaticMoveEntityService,
) -> LocalProjectIndexMoveContentUpdater:
    return LocalProjectIndexMoveContentUpdater(
        entity_service=cast(LocalMoveEntityService, entity_service),
        file_service=FileService(tmp_path),
    )


def _moved_file(new_path: str = "archive/renamed.md") -> ProjectIndexMovedFile:
    return ProjectIndexMovedFile(
        entity_id=10,
        old_path="notes/original.md",
        new_path=new_path,
        old_permalink="main/notes/original",
    )


def _session() -> AsyncSession:
    return cast(AsyncSession, object())


def test_merged_frontmatter_markdown_updates_existing_frontmatter() -> None:
    merged = merged_frontmatter_markdown(
        "---\ntitle: Original\npermalink: old\n---\n\n# Body\n",
        {"permalink": "new"},
    )

    assert merged.startswith("---\n")
    assert "title: Original" in merged
    assert "permalink: new" in merged
    assert "permalink: old" not in merged
    assert merged.endswith("# Body")


def test_merged_frontmatter_markdown_creates_frontmatter_when_missing() -> None:
    merged = merged_frontmatter_markdown("# Plain Body\n", {"permalink": "new"})

    assert merged == "---\npermalink: new\n---\n\n# Plain Body"


def test_merged_frontmatter_markdown_treats_malformed_yaml_as_plain_markdown() -> None:
    malformed = "---\ntitle: [unclosed\n---\n\n# Body\n"

    merged = merged_frontmatter_markdown(malformed, {"permalink": "new"})

    assert merged.startswith("---\npermalink: new\n---\n\n")
    # The malformed block is preserved as body content, not silently dropped.
    assert "[unclosed" in merged


@pytest.mark.asyncio
async def test_plan_moved_file_content_requires_app_config(tmp_path: Path) -> None:
    updater = _updater(tmp_path, StaticMoveEntityService(app_config=None))

    with pytest.raises(RuntimeError, match="require app_config"):
        await updater.plan_moved_file_content(_session(), _moved_file())


@pytest.mark.asyncio
async def test_plan_moved_file_content_respects_permalink_policy(tmp_path: Path) -> None:
    disabled = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig(disable_permalinks=True)),
    )
    assert await disabled.plan_moved_file_content(_session(), _moved_file()) is None

    no_move_updates = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig(update_permalinks_on_move=False)),
    )
    assert await no_move_updates.plan_moved_file_content(_session(), _moved_file()) is None


@pytest.mark.asyncio
async def test_plan_moved_file_content_skips_non_markdown_and_unchanged_permalinks(
    tmp_path: Path,
) -> None:
    updater = _updater(tmp_path, StaticMoveEntityService(app_config=MovePermalinkConfig()))
    assert (
        await updater.plan_moved_file_content(_session(), _moved_file(new_path="asset.pdf")) is None
    )

    unchanged = _updater(
        tmp_path,
        StaticMoveEntityService(
            app_config=MovePermalinkConfig(),
            permalink="main/notes/original",
        ),
    )
    assert await unchanged.plan_moved_file_content(_session(), _moved_file()) is None


@pytest.mark.asyncio
async def test_plan_moved_file_content_plans_without_writing_then_write_persists(
    tmp_path: Path,
) -> None:
    """Planning must not mutate the file; the write persists exactly the planned bytes."""
    moved_file = _moved_file()
    file_path = tmp_path / moved_file.new_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    original_content = "---\ntitle: Renamed\npermalink: main/notes/original\n---\n\n# Renamed\n"
    file_path.write_text(original_content, encoding="utf-8")

    updater = _updater(tmp_path, StaticMoveEntityService(app_config=MovePermalinkConfig()))
    content_update = await updater.plan_moved_file_content(_session(), moved_file)

    assert content_update is not None
    assert content_update.permalink == "main/archive/renamed"
    assert "permalink: main/archive/renamed" in content_update.markdown_content
    assert content_update.checksum == await compute_checksum(content_update.markdown_content)
    # Planning left the file untouched.
    assert file_path.read_text(encoding="utf-8") == original_content

    await updater.write_moved_file_content(moved_file, content_update)

    assert file_path.read_text(encoding="utf-8") == content_update.markdown_content
