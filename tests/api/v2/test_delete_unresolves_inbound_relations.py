"""Deleting a note must not erase the relations other notes point at it.

Before this fix, deleting note Beta took every relation row aimed at Beta with it --
even though note Alpha's markdown still said `[[Beta]]`. The text asserted an edge and
the graph denied one, so a "find the links that lead nowhere" report came back clean
over a vault full of them. Alpha only got its row back, correctly marked unresolved, if
something happened to re-index Alpha, and nothing schedules that.

`to_id` has always been nullable, and unresolved-with-`to_name`-preserved is the state
the indexer already produces for a link to a note that does not exist yet. These tests
pin the two HTTP delete paths onto that state -- the single-entity delete and the
directory delete -- plus the unique-constraint question that SET NULL raises.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from basic_memory import db
from basic_memory.indexing.forward_reference_resolution import (
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
    run_forward_reference_resolution,
)
from basic_memory.models import Project, Relation
from basic_memory.schemas.v2.entity import EntityResponseV2


async def _relations_from(session_maker, entity_repository, file_path: str):
    """Load one note's outgoing relations straight from the database."""
    async with db.scoped_session(session_maker) as session:
        source = await entity_repository.get_by_file_path(session, file_path)
        assert source is not None
        rows = (
            (await session.execute(select(Relation).where(Relation.from_id == source.id)))
            .scalars()
            .all()
        )
        return source.id, list(rows)


async def _resolve_forward_references(session_maker, project_id: int) -> None:
    """Run the deferred forward-reference pass the API only schedules.

    The v2 API test app stubs the resolution scheduler out, so nothing runs it for us.
    This is the same pass the real scheduler drives.
    """
    relation_source = RepositoryForwardReferenceRelationSource(
        session_maker=session_maker,
        project_id=project_id,
    )
    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=session_maker,
        project_id=project_id,
    )
    await run_forward_reference_resolution(
        runtime,
        await relation_source.list_unresolved_forward_references(),
    )


@pytest.mark.asyncio
async def test_delete_entity_unresolves_inbound_relation_and_recreate_relinks(
    client: AsyncClient,
    v2_project_url,
    entity_repository,
    session_maker,
    test_project: Project,
):
    """API single-entity delete: the inbound edge survives unresolved, and comes back."""
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Beta", "directory": "unresolve", "content": "# Beta"},
    )
    assert response.status_code == 202
    beta = EntityResponseV2.model_validate(response.json())

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Alpha",
            "directory": "unresolve",
            "content": "# Alpha\n\n- links_to [[Beta]]",
        },
    )
    assert response.status_code == 202

    alpha_id, relations = await _relations_from(
        session_maker, entity_repository, "unresolve/Alpha.md"
    )
    assert len(relations) == 1
    assert relations[0].to_id == beta.id
    assert relations[0].to_name == "Beta"

    response = await client.delete(f"{v2_project_url}/knowledge/entities/{beta.external_id}")
    assert response.status_code == 202

    _, after_delete = await _relations_from(session_maker, entity_repository, "unresolve/Alpha.md")
    assert len(after_delete) == 1, "the inbound relation must survive its target's deletion"
    assert after_delete[0].from_id == alpha_id
    assert after_delete[0].to_id is None
    assert after_delete[0].to_name == "Beta"
    assert after_delete[0].relation_type == "links_to"

    # Recreating the target re-links the row through the existing forward-reference
    # pass. Nothing new was built for this; unresolved was already a supported state.
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Beta", "directory": "unresolve", "content": "# Beta again"},
    )
    assert response.status_code == 202
    recreated_beta = EntityResponseV2.model_validate(response.json())

    await _resolve_forward_references(session_maker, test_project.id)

    _, after_recreate = await _relations_from(
        session_maker, entity_repository, "unresolve/Alpha.md"
    )
    assert len(after_recreate) == 1
    assert after_recreate[0].to_id == recreated_beta.id
    assert after_recreate[0].to_name == "Beta"


@pytest.mark.asyncio
async def test_delete_directory_unresolves_inbound_relations_from_outside(
    client: AsyncClient,
    v2_project_url,
    entity_repository,
    session_maker,
    test_project: Project,
):
    """Directory delete: a keeper outside the directory keeps its edge, unresolved."""
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Dir Target",
            "directory": "unresolve-dir",
            "content": "# Dir Target",
        },
    )
    assert response.status_code == 202
    target = EntityResponseV2.model_validate(response.json())

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Dir Keeper",
            "directory": "unresolve-keep",
            "content": "# Dir Keeper\n\n- links_to [[Dir Target]]",
        },
    )
    assert response.status_code == 202

    _, before = await _relations_from(
        session_maker, entity_repository, "unresolve-keep/Dir Keeper.md"
    )
    assert len(before) == 1
    assert before[0].to_id == target.id

    response = await client.post(
        f"{v2_project_url}/knowledge/delete-directory",
        json={"directory": "unresolve-dir"},
    )
    assert response.status_code == 200

    _, after = await _relations_from(
        session_maker, entity_repository, "unresolve-keep/Dir Keeper.md"
    )
    assert len(after) == 1, "the keeper's relation must survive the directory delete"
    assert after[0].to_id is None
    assert after[0].to_name == "Dir Target"
    assert after[0].relation_type == "links_to"

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Dir Target",
            "directory": "unresolve-dir",
            "content": "# Dir Target again",
        },
    )
    assert response.status_code == 202
    recreated = EntityResponseV2.model_validate(response.json())

    await _resolve_forward_references(session_maker, test_project.id)

    _, relinked = await _relations_from(
        session_maker, entity_repository, "unresolve-keep/Dir Keeper.md"
    )
    assert len(relinked) == 1
    assert relinked[0].to_id == recreated.id


@pytest.mark.asyncio
async def test_two_deleted_targets_unresolve_without_unique_constraint_collision(
    client: AsyncClient,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Unresolving two rows to the same (from_id, NULL, relation_type) must not collide.

    `uix_relation_from_id_to_id` covers (from_id, to_id, relation_type), so the obvious
    worry about SET NULL is two inbound rows from one source colliding once both targets
    are gone. They do not: NULL is distinct from NULL under both backends' unique
    constraints, and `uix_relation_from_id_to_name` still tells the two rows apart by the
    link text the author wrote.
    """
    for title in ("Collide One", "Collide Two"):
        response = await client.post(
            f"{v2_project_url}/knowledge/entities",
            json={"title": title, "directory": "collide", "content": f"# {title}"},
        )
        assert response.status_code == 202

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Collide Source",
            "directory": "collide-src",
            "content": "# Collide Source\n\n- links_to [[Collide One]]\n- links_to [[Collide Two]]",
        },
    )
    assert response.status_code == 202

    _, before = await _relations_from(
        session_maker, entity_repository, "collide-src/Collide Source.md"
    )
    assert len(before) == 2
    assert all(relation.to_id is not None for relation in before)

    response = await client.post(
        f"{v2_project_url}/knowledge/delete-directory",
        json={"directory": "collide"},
    )
    assert response.status_code == 200

    _, after = await _relations_from(
        session_maker, entity_repository, "collide-src/Collide Source.md"
    )
    assert len(after) == 2, "both inbound relations must survive, unresolved"
    assert [relation.to_id for relation in after] == [None, None]
    assert sorted(relation.to_name for relation in after) == ["Collide One", "Collide Two"]
