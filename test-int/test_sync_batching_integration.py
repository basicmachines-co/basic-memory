"""Integration coverage for batched sync indexing."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.repository import (
    EntityRepository,
    ObservationRepository,
    ProjectRepository,
    RelationRepository,
)
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.services import FileService
from basic_memory.services.entity_service import EntityService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync.sync_service import MAX_CONSECUTIVE_FAILURES, SyncService


async def _create_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def _create_binary_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def _build_sync_service(
    project_root: Path,
    engine_factory,
    app_config,
    test_project,
) -> SyncService:
    _, session_maker = engine_factory

    entity_repository = EntityRepository(session_maker, project_id=test_project.id)
    observation_repository = ObservationRepository(session_maker, project_id=test_project.id)
    relation_repository = RelationRepository(session_maker, project_id=test_project.id)
    project_repository = ProjectRepository(session_maker)
    search_repository = create_search_repository(session_maker, project_id=test_project.id)

    entity_parser = EntityParser(project_root)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(project_root, markdown_processor)
    search_service = SearchService(search_repository, entity_repository, file_service)
    await search_service.init_search_index()
    link_resolver = LinkResolver(entity_repository, search_service)

    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


@pytest.mark.asyncio
async def test_sync_batching_handles_large_single_file_batches_and_resolves_forward_refs(
    engine_factory,
    app_config,
    test_project,
):
    app_config.index_batch_size = 2
    app_config.index_batch_max_bytes = 256

    project_root = Path(test_project.path)
    sync_service = await _build_sync_service(project_root, engine_factory, app_config, test_project)

    await _create_file(
        project_root / "notes/alpha.md",
        dedent(
            """
            ---
            title: Alpha
            type: note
            ---
            # Alpha

            - depends_on [[Target]]
            """
        ).strip(),
    )
    await _create_file(
        project_root / "notes/large.md",
        dedent(
            f"""
            ---
            title: Large
            type: note
            ---
            # Large

            {"x" * 2048}
            """
        ).strip(),
    )
    await _create_file(
        project_root / "notes/target.md",
        dedent(
            """
            ---
            title: Target
            type: note
            ---
            # Target
            """
        ).strip(),
    )

    report = await sync_service.sync(
        project_root,
        project_name=test_project.name,
        force_full=True,
    )

    alpha = await sync_service.entity_repository.get_by_file_path("notes/alpha.md")
    large = await sync_service.entity_repository.get_by_file_path("notes/large.md")
    target = await sync_service.entity_repository.get_by_file_path("notes/target.md")

    assert report.total == 3
    assert alpha is not None
    assert large is not None
    assert target is not None
    assert large.size is not None
    assert large.size > app_config.index_batch_max_bytes
    assert len(alpha.outgoing_relations) == 1
    assert alpha.outgoing_relations[0].to_id == target.id


@pytest.mark.asyncio
async def test_sync_batching_circuit_breaker_skips_unchanged_broken_markdown_after_threshold(
    engine_factory,
    app_config,
    test_project,
):
    app_config.index_batch_size = 1
    app_config.index_batch_max_bytes = 256

    project_root = Path(test_project.path)
    sync_service = await _build_sync_service(project_root, engine_factory, app_config, test_project)

    await _create_binary_file(project_root / "notes/broken.md", b"\xff\xfe\xfd")

    last_report = None
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        last_report = await sync_service.sync(
            project_root,
            project_name=test_project.name,
            force_full=True,
        )

    assert last_report is not None
    assert [skipped.path for skipped in last_report.skipped_files] == ["notes/broken.md"]
    assert sync_service._file_failures["notes/broken.md"].count == MAX_CONSECUTIVE_FAILURES

    await _create_file(
        project_root / "notes/good.md",
        dedent(
            """
            ---
            title: Good
            type: note
            ---
            # Good
            """
        ).strip(),
    )

    report = await sync_service.sync(
        project_root,
        project_name=test_project.name,
        force_full=True,
    )

    good = await sync_service.entity_repository.get_by_file_path("notes/good.md")
    broken = await sync_service.entity_repository.get_by_file_path("notes/broken.md")

    assert [skipped.path for skipped in report.skipped_files] == ["notes/broken.md"]
    assert good is not None
    assert broken is None
