"""PostgreSQL concurrency coverage for generation-versioned relation persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from basic_memory import db
from basic_memory.index.local_dependencies import build_local_markdown_file_indexer
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.index_batch_runtime import build_default_index_batch_runtime
from basic_memory.indexing.models import IndexInputFile
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.repository import (
    EntityRepository,
    NoteContentRepository,
    ObservationRepository,
    RelationRepository,
)
from basic_memory.services import EntityService, FileService
from basic_memory.services.link_resolver import LinkResolver


async def write_note(path: Path, *, title: str, target: str | None, version: str) -> None:
    """Write one deterministic note generation for the concurrency exercise."""
    relation = f"\n- links_to [[{target}]]\n" if target is not None else ""
    content = f"---\ntitle: {title}\ntype: note\n---\n\n# {title}\n\n{version}\n{relation}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def load_input(file_service, path: str) -> IndexInputFile:
    """Load the current file bytes through the production storage adapter."""
    metadata = await file_service.get_file_metadata(path)
    return IndexInputFile(
        path=path,
        size=metadata.size,
        checksum=await file_service.compute_checksum(path),
        content_type=file_service.content_type(path),
        last_modified=metadata.modified_at,
        created_at=metadata.created_at,
        content=await file_service.read_file_bytes(path),
    )


class EntityUpdateRendezvous:
    """Release two source-entity transactions only after both hold their row lock."""

    def __init__(self) -> None:
        self.arrivals = 0
        self.ready = asyncio.Event()

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=5)


@pytest.mark.asyncio
async def test_mutual_relation_generations_complete_under_concurrent_persistence(
    engine_factory,
    app_config,
    search_service,
    project_config,
    test_project,
    monkeypatch,
) -> None:
    """Mutual batch and single-file updates finish without the old lock cycle."""
    engine, session_maker = engine_factory
    if engine.dialect.name != "postgresql":
        pytest.skip("row-lock deadlock regression requires PostgreSQL")

    app_config.disable_permalinks = True
    entity_repository = EntityRepository(project_id=test_project.id)
    observation_repository = ObservationRepository(project_id=test_project.id)
    relation_repository = RelationRepository(project_id=test_project.id)
    entity_parser = EntityParser(project_config.home)
    file_service = FileService(
        project_config.home,
        MarkdownProcessor(entity_parser, app_config=app_config),
        app_config=app_config,
    )
    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=LinkResolver(
            entity_repository,
            search_service,
            session_maker=session_maker,
            app_config=app_config,
        ),
        session_maker=session_maker,
        search_service=search_service,
        app_config=app_config,
    )
    alpha_path = "notes/alpha.md"
    beta_path = "notes/beta.md"
    absolute_alpha_path = project_config.home / alpha_path
    absolute_beta_path = project_config.home / beta_path

    runtime = build_default_index_batch_runtime(
        project_id=test_project.id,
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_writer=search_service,
        frontmatter_storage=file_service,
        content_type_provider=file_service,
        session_maker=session_maker,
    )
    batch_indexer = cast(BatchIndexer, runtime.batch_indexer)
    local_indexer = build_local_markdown_file_indexer(
        project_id=test_project.id,
        file_service=file_service,
        session_maker=session_maker,
        entity_repository=entity_repository,
        batch_indexer=batch_indexer,
        search_service=search_service,
    )

    # Seed committed targets so the historical delete/insert path would resolve
    # both mutual links while each transaction still held its own Entity row lock.
    await write_note(absolute_alpha_path, title="Alpha", target=None, version="Seed alpha")
    await write_note(absolute_beta_path, title="Beta", target=None, version="Seed beta")
    seed_result = await runtime.index_loaded_files(
        {
            alpha_path: await load_input(file_service, alpha_path),
            beta_path: await load_input(file_service, beta_path),
        },
        max_concurrent=2,
    )
    assert seed_result.errors == []

    original_update = entity_service.update_entity_and_observations

    # Batch direction: both persistence transactions hold their source Entity lock
    # before continuing. The old path then inserted A -> B and B -> A in those same
    # transactions, creating the production FK lock cycle. Relation publication now
    # begins only after both short entity transactions commit.
    batch_rendezvous = EntityUpdateRendezvous()

    async def synchronized_batch_update(*args, **kwargs):
        entity = await original_update(*args, **kwargs)
        await batch_rendezvous.wait()
        return entity

    monkeypatch.setattr(
        entity_service,
        "update_entity_and_observations",
        synchronized_batch_update,
    )
    await write_note(
        absolute_alpha_path,
        title="Alpha",
        target="Beta",
        version="Batch generation",
    )
    await write_note(
        absolute_beta_path,
        title="Beta",
        target="Alpha",
        version="Batch generation",
    )
    batch_result = await asyncio.wait_for(
        runtime.index_loaded_files(
            {
                alpha_path: await load_input(file_service, alpha_path),
                beta_path: await load_input(file_service, beta_path),
            },
            max_concurrent=2,
        ),
        timeout=15,
    )
    assert batch_rendezvous.arrivals == 2
    assert batch_result.errors == []
    assert batch_result.relations_unresolved == 0

    # Single-file direction: two independent retry loops take the same source-lock
    # rendezvous, then claim and publish their own next generations concurrently.
    single_file_rendezvous = EntityUpdateRendezvous()

    async def synchronized_single_file_update(*args, **kwargs):
        entity = await original_update(*args, **kwargs)
        await single_file_rendezvous.wait()
        return entity

    monkeypatch.setattr(
        entity_service,
        "update_entity_and_observations",
        synchronized_single_file_update,
    )
    await write_note(
        absolute_alpha_path,
        title="Alpha",
        target="Beta",
        version="Single-file generation",
    )
    await write_note(
        absolute_beta_path,
        title="Beta",
        target="Alpha",
        version="Single-file generation",
    )
    single_file_results = await asyncio.wait_for(
        asyncio.gather(
            local_indexer.index_markdown_file(alpha_path, source="test"),
            local_indexer.index_markdown_file(beta_path, source="test"),
        ),
        timeout=15,
    )
    assert single_file_rendezvous.arrivals == 2
    assert {result.file_path for result in single_file_results} == {alpha_path, beta_path}

    async with db.scoped_session(session_maker) as session:
        alpha = await entity_repository.get_by_file_path(session, alpha_path)
        beta = await entity_repository.get_by_file_path(session, beta_path)
        assert alpha is not None
        assert beta is not None
        note_contents = await NoteContentRepository(project_id=test_project.id).find_by_ids(
            session,
            [alpha.id, beta.id],
        )
        relations = await relation_repository.find_all(session)

    generation_by_entity_id = {
        note_content.entity_id: note_content.db_version for note_content in note_contents
    }
    assert generation_by_entity_id == {alpha.id: 3, beta.id: 3}
    assert {
        (
            relation.from_id,
            relation.to_id,
            relation.to_name,
            relation.relation_type,
            relation.generation,
        )
        for relation in relations
    } == {
        (alpha.id, beta.id, "Beta", "links_to", generation_by_entity_id[alpha.id]),
        (beta.id, alpha.id, "Alpha", "links_to", generation_by_entity_id[beta.id]),
    }
