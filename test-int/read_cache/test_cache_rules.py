"""The read-cache rules, end to end through the API against a real Redis.

Each test states one rule from the read-cache design and drives it through real
routes, real writes and real Redis. ``X-Redis-Cache`` (set by fastapi-redis-sdk)
reports whether a response came from the cache.
"""

from __future__ import annotations

from typing import Any, Protocol

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from basic_memory.index.note_content_materialization import drain_pending_materializations
from basic_memory.models import Project
from basic_memory.read_cache.redis import RedisReadCache, create_redis_read_cache_client

pytestmark = pytest.mark.redis

# Matches the test-only header the cached_app fixture's scope resolver reads.
TEST_USER_HEADER = "x-test-user"


class CachedApp(Protocol):
    """Structural type for the cached_app fixture."""

    app: FastAPI
    cache: RedisReadCache

STATUS = "x-redis-cache"


def _status(response: Response) -> str | None:
    return response.headers.get(STATUS)


async def _create(client: AsyncClient, project_url: str, title: str, body: str = "") -> dict[str, Any]:
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": title, "directory": "rules", "content": f"# {title}\n\n{body}"},
    )
    assert response.status_code == 202, response.text
    await drain_pending_materializations()
    return response.json()


async def _query(client: AsyncClient, url: str, body: dict[str, Any], **kwargs: Any) -> Response:
    return await client.request("QUERY", url, json=body, **kwargs)


@pytest.fixture
def project_url(test_project: Project) -> str:
    return f"/v2/projects/{test_project.external_id}"


@pytest.mark.asyncio
async def test_a_note_read_misses_then_hits_then_revalidates(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: repeat reads are served from cache, and browsers revalidate with ETag."""
    note = await _create(client, project_url, "Cached Once")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    first = await client.get(url)
    second = await client.get(url)
    revalidated = await client.get(url, headers={"if-none-match": second.headers["etag"]})

    assert (_status(first), _status(second)) == ("MISS", "HIT")
    assert second.json() == first.json()
    assert revalidated.status_code == 304


@pytest.mark.asyncio
async def test_a_body_edit_retires_that_note_and_content_reads_only(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: a body edit retires the edited note, search and listings, nothing else."""
    edited = await _create(client, project_url, "Edited Note", "Version one.")
    untouched = await _create(client, project_url, "Untouched Note", "Stays cached.")
    edited_url = f"{project_url}/knowledge/entities/{edited['external_id']}"
    untouched_url = f"{project_url}/knowledge/entities/{untouched['external_id']}"
    resolve_body = {"identifier": untouched["permalink"]}
    search_body = {"text": "cached"}

    for _ in range(2):
        await client.get(edited_url)
        await client.get(untouched_url)
        await _query(client, f"{project_url}/knowledge/resolve", resolve_body)
        await client.get(f"{project_url}/directory/structure")
        await _query(client, f"{project_url}/search/", search_body)

    patch = await client.patch(edited_url, json={"operation": "append", "content": "\n\nVersion two."})
    assert patch.status_code == 202, patch.text
    await drain_pending_materializations()

    edited_read = await client.get(edited_url)
    assert _status(edited_read) == "MISS"
    assert "Version two." in edited_read.json()["content"]
    assert _status(await _query(client, f"{project_url}/search/", search_body)) == "MISS"

    assert _status(await client.get(untouched_url)) == "HIT"
    assert _status(await _query(client, f"{project_url}/knowledge/resolve", resolve_body)) == "HIT"
    assert _status(await client.get(f"{project_url}/directory/structure")) == "HIT"


@pytest.mark.asyncio
async def test_adding_a_link_keeps_the_linked_note_cached(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: only reads whose bytes change are retired.

    A markdown note's response lists its outgoing links only, so linking to it from
    another note leaves its cached response correct.
    """
    target = await _create(client, project_url, "Link Target")
    source = await _create(client, project_url, "Link Source")
    target_url = f"{project_url}/knowledge/entities/{target['external_id']}"
    source_url = f"{project_url}/knowledge/entities/{source['external_id']}"
    for _ in range(2):
        await client.get(target_url)
        await client.get(source_url)

    patch = await client.patch(
        source_url,
        json={"operation": "append", "content": "\n\n- relates_to [[Link Target]]"},
    )
    assert patch.status_code == 202, patch.text
    await drain_pending_materializations()

    source_read = await client.get(source_url)
    target_read = await client.get(target_url)

    assert _status(source_read) == "MISS"
    assert "[[Link Target]]" in source_read.json()["content"]
    assert _status(target_read) == "HIT"


@pytest.mark.asyncio
async def test_a_rename_retires_the_whole_project(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: a rename changes name lookups and links shown elsewhere, so all reads go."""
    renamed = await _create(client, project_url, "Before Rename")
    bystander = await _create(client, project_url, "Bystander")
    bystander_url = f"{project_url}/knowledge/entities/{bystander['external_id']}"
    resolve_body = {"identifier": bystander["permalink"]}
    for _ in range(2):
        await client.get(bystander_url)
        await _query(client, f"{project_url}/knowledge/resolve", resolve_body)

    put = await client.put(
        f"{project_url}/knowledge/entities/{renamed['external_id']}",
        json={"title": "After Rename", "directory": "rules", "content": "# After Rename\n"},
    )
    assert put.status_code == 202, put.text
    await drain_pending_materializations()

    assert _status(await client.get(bystander_url)) == "MISS"
    assert _status(await _query(client, f"{project_url}/knowledge/resolve", resolve_body)) == "MISS"


@pytest.mark.asyncio
async def test_a_fuzzy_resolution_is_never_cached(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: a fuzzy name match can change with any content, so it is not stored."""
    await _create(client, project_url, "Quarterly Planning Notes")
    body = {"identifier": "quarterly planning"}

    first = await _query(client, f"{project_url}/knowledge/resolve", body)
    second = await _query(client, f"{project_url}/knowledge/resolve", body)

    assert first.status_code == 200, first.text
    assert first.json()["resolution_method"] == "search"
    assert _status(second) != "HIT"


@pytest.mark.asyncio
async def test_users_never_share_entries(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: entries are keyed per user, so one user's response never reaches another."""
    note = await _create(client, project_url, "Per User")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    await client.get(url, headers={TEST_USER_HEADER: "alice"})
    alice_again = await client.get(url, headers={TEST_USER_HEADER: "alice"})
    bob = await client.get(url, headers={TEST_USER_HEADER: "bob"})

    assert _status(alice_again) == "HIT"
    assert _status(bob) == "MISS"


@pytest.mark.asyncio
async def test_reset_retires_every_entry(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: after a deploy, one reset retires every cached read."""
    note = await _create(client, project_url, "Before Reset")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"
    await client.get(url)
    assert _status(await client.get(url)) == "HIT"

    await cached_app.cache.reset_all()

    assert _status(await client.get(url)) == "MISS"


@pytest.mark.asyncio
async def test_unavailable_redis_falls_back_to_the_database(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: with Redis down, reads are answered by the database and nothing fails."""
    note = await _create(client, project_url, "Redis Down")
    unreachable = RedisReadCache(
        client=create_redis_read_cache_client("redis://127.0.0.1:1/0", socket_timeout=0.05),
        namespace="unreachable",
    )

    async def unreachable_scope(request: Any) -> Any:
        from basic_memory.read_cache.routes import ReadCacheScope

        return ReadCacheScope(cache=unreachable, user_key="")

    cached_app.app.state.read_cache_scope_resolver = unreachable_scope
    try:
        response = await client.get(f"{project_url}/knowledge/entities/{note['external_id']}")
    finally:
        await unreachable.client.aclose()

    assert response.status_code == 200
    assert response.json()["title"] == "Redis Down"
    assert _status(response) != "HIT"


@pytest.mark.asyncio
async def test_search_and_resolve_accept_query_only(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: search and resolve are QUERY reads; POST is not a route."""
    search = await client.post(f"{project_url}/search/", json={"text": "anything"})
    resolve = await client.post(f"{project_url}/knowledge/resolve", json={"identifier": "x"})

    assert search.status_code == 405
    assert resolve.status_code == 405
