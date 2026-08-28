"""The file watcher must not erase the relations pointing at a deleted note.

The watcher takes a different route to the same place as the API: a bulk SQL
`DELETE` rather than an ORM `session.delete`. That is exactly why it gets its own
test -- the database constraint and the ORM cascade were two separate mechanisms
doing the same damage, and fixing one without the other just moves the bug.

Measured shape of the bug: note Alpha links to note Beta, Beta's file is removed on
disk, the watcher notices, and Alpha's relation row disappears along with Beta --
while Alpha's markdown still says `[[Beta]]`.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_service import WatchService
from basic_memory.indexing.forward_reference_resolution import (
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
    run_forward_reference_resolution,
)
from basic_memory.models.knowledge import Entity, Relation


BETA_NOTE = """---
type: note
title: Beta
---
# Beta
"""

ALPHA_NOTE = """---
type: note
title: Alpha
---
# Alpha

- links_to [[Beta]]
"""


async def create_test_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_watch_delete_unresolves_inbound_relation_and_recreate_relinks(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Watcher delete path: the inbound relation survives unresolved, and re-links."""
    alpha_path = project_config.home / "alpha.md"
    beta_path = project_config.home / "beta.md"
    await create_test_file(beta_path, BETA_NOTE)
    await create_test_file(alpha_path, ALPHA_NOTE)

    indexed = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert indexed.enqueued_files == 2

    async with db.scoped_session(session_maker) as session:
        alpha = await entity_repository.get_by_file_path(session, "alpha.md")
        beta = await entity_repository.get_by_file_path(session, "beta.md")
        assert alpha is not None
        assert beta is not None
        assert len(alpha.outgoing_relations) == 1
        assert alpha.outgoing_relations[0].to_id == beta.id
        alpha_id = alpha.id
        beta_id = beta.id

    beta_path.unlink()

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )
    await watch_service.handle_changes(test_project, {(Change.deleted, str(beta_path))})

    async with db.scoped_session(session_maker) as session:
        assert await session.get(Entity, beta_id) is None
        rows = (
            (await session.execute(select(Relation).where(Relation.from_id == alpha_id)))
            .scalars()
            .all()
        )

    assert len(rows) == 1, "the inbound relation must survive the watcher's bulk delete"
    assert rows[0].to_id is None
    assert rows[0].to_name == "Beta"
    assert rows[0].relation_type == "links_to"

    # Alpha's markdown never changed, and now the graph agrees with it again.
    assert "[[Beta]]" in alpha_path.read_text(encoding="utf-8")

    # Put Beta back. Forward-reference resolution -- which already existed for links
    # written before their target -- picks the row up and re-links it.
    await create_test_file(beta_path, BETA_NOTE)
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )

    relation_source = RepositoryForwardReferenceRelationSource(
        session_maker=session_maker,
        project_id=test_project.id,
    )
    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=session_maker,
        project_id=test_project.id,
    )
    await run_forward_reference_resolution(
        runtime,
        await relation_source.list_unresolved_forward_references(),
    )

    async with db.scoped_session(session_maker) as session:
        recreated_beta = await entity_repository.get_by_file_path(session, "beta.md")
        assert recreated_beta is not None
        rows = (
            (await session.execute(select(Relation).where(Relation.from_id == alpha_id)))
            .scalars()
            .all()
        )

    assert len(rows) == 1
    assert rows[0].to_id == recreated_beta.id
    assert rows[0].to_name == "Beta"
