"""The read-cache rules, end to end through the API against a real Redis.

Each test states one rule from the read-cache design and drives it through real
routes, real writes and real Redis. ``X-Redis-Cache`` (set by fastapi-redis-sdk)
reports whether a response came from the cache.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from logfire.testing import CaptureLogfire, capfire as capfire

from basic_memory.index.note_content_materialization import drain_pending_materializations
from basic_memory.models import Project
from basic_memory.read_cache.contract import ReadCacheUnavailable
from basic_memory.read_cache.policy import READ_CACHE_MAX_PAYLOAD_BYTES
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


async def _create(
    client: AsyncClient, project_url: str, title: str, body: str = ""
) -> dict[str, Any]:
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": title, "directory": "rules", "content": f"# {title}\n\n{body}"},
    )
    assert response.status_code == 202, response.text
    await drain_pending_materializations()
    return response.json()


async def _query(client: AsyncClient, url: str, body: dict[str, Any], **kwargs: Any) -> Response:
    return await client.request("QUERY", url, json=body, **kwargs)


async def _stored_entries(cache: RedisReadCache, operation: str = "*") -> set[bytes]:
    """Cached response keys in Redis: not version tokens or entry indexes."""
    return {
        key
        async for key in cache.client.scan_iter(match=f"{cache.prefix}:*:{operation}:*")
        if b":version:" not in key and b":entries:" not in key
    }


async def _project_set_members(cache: RedisReadCache) -> set[bytes]:
    """Every entry key the project-epoch indexes list."""
    indexes = [
        index async for index in cache.client.scan_iter(match=f"{cache.prefix}:*:entries:project:*")
    ]
    async with cache.client.pipeline(transaction=False) as pipe:
        for index in indexes:
            pipe.zrange(index, 0, -1)
        listed: list[list[bytes]] = await pipe.execute()
    return {key for members in listed for key in members}


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

    patch = await client.patch(
        edited_url, json={"operation": "append", "content": "\n\nVersion two."}
    )
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
async def test_a_body_edit_deletes_the_retired_note_entry_from_redis(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: retired entries free their memory at once instead of waiting out the TTL."""
    edited = await _create(client, project_url, "Edited And Deleted", "Version one.")
    untouched = await _create(client, project_url, "Kept In Redis", "Stays cached.")
    edited_url = f"{project_url}/knowledge/entities/{edited['external_id']}"
    untouched_url = f"{project_url}/knowledge/entities/{untouched['external_id']}"

    await client.get(edited_url)
    edited_entries = await _stored_entries(cached_app.cache, "entity")
    await client.get(untouched_url)
    untouched_entries = await _stored_entries(cached_app.cache, "entity") - edited_entries
    assert len(edited_entries) == 1 and len(untouched_entries) == 1

    patch = await client.patch(
        edited_url, json={"operation": "append", "content": "\n\nVersion two."}
    )
    assert patch.status_code == 202, patch.text
    await drain_pending_materializations()

    assert await _stored_entries(cached_app.cache, "entity") == untouched_entries
    # The project set lists live entries only, so a project whose epoch never
    # changes does not collect a key for every retired response.
    assert await _project_set_members(cached_app.cache) == untouched_entries
    assert _status(await client.get(untouched_url)) == "HIT"


@pytest.mark.asyncio
async def test_a_rename_deletes_every_entry_in_the_project_from_redis(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: retiring the whole project deletes every entry it held, of every kind."""
    renamed = await _create(client, project_url, "Renamed Away")
    bystander = await _create(client, project_url, "Bystander Entry")
    await client.get(f"{project_url}/knowledge/entities/{bystander['external_id']}")
    await _query(client, f"{project_url}/knowledge/resolve", {"identifier": bystander["permalink"]})
    await _query(client, f"{project_url}/search/", {"text": "bystander"})
    await client.get(f"{project_url}/directory/structure")
    assert len(await _stored_entries(cached_app.cache)) >= 4

    put = await client.put(
        f"{project_url}/knowledge/entities/{renamed['external_id']}",
        json={"title": "Renamed Here", "directory": "rules", "content": "# Renamed Here\n"},
    )
    assert put.status_code == 202, put.text
    await drain_pending_materializations()

    assert await _stored_entries(cached_app.cache) == set()


@pytest.mark.asyncio
async def test_a_no_store_request_is_answered_without_touching_the_cache(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: ``Cache-Control: no-store`` reads from the database and stores nothing."""
    note = await _create(client, project_url, "Never Stored")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    response = await client.get(url, headers={"cache-control": "no-store"})

    assert response.status_code == 200
    assert _status(response) is None
    assert await _stored_entries(cached_app.cache, "entity") == set()


@pytest.mark.asyncio
async def test_an_entry_that_cannot_be_tracked_is_served_but_not_stored(
    cached_app: CachedApp,
    client: AsyncClient,
    project_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule: an entry invalidation could not find is never stored.

    Making only the tracking write fail needs an injected edge case: the version
    lookup and the tracking write share one real Redis.
    """
    note = await _create(client, project_url, "Untracked")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    async def fail_tracking(*_args: object) -> None:
        raise ReadCacheUnavailable("injected tracking failure")

    monkeypatch.setattr(cached_app.cache, "track_entry", fail_tracking)

    first = await client.get(url)
    second = await client.get(url)

    assert first.status_code == 200 and second.status_code == 200
    assert _status(second) != "HIT"
    assert await _stored_entries(cached_app.cache, "entity") == set()


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


@pytest.mark.asyncio
async def test_each_slice_of_a_note_is_its_own_entry(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: a hit returns exactly what the database would; slice params are in the key."""
    note = await _create(client, project_url, "Sliced Note", "## Ops\no1\n\n## Dev\nd1")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    full = await client.get(url)
    section = await client.get(url, params={"section": "Ops"})
    full_again = await client.get(url)
    section_again = await client.get(url, params={"section": "Ops"})

    assert (_status(full), _status(section)) == ("MISS", "MISS")
    assert (_status(full_again), _status(section_again)) == ("HIT", "HIT")
    assert full_again.json()["content"] == full.json()["content"]
    assert section_again.json()["content"] == section.json()["content"]
    assert section.json()["content"].strip() == "## Ops\no1"


@pytest.mark.asyncio
async def test_each_cached_read_reports_one_span_with_its_outcome(
    cached_app: CachedApp, client: AsyncClient, project_url: str, capfire: CaptureLogfire
) -> None:
    """Rule: every cached read is observable as one span with outcome and group kind."""
    note = await _create(client, project_url, "Observed Read")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"
    capfire.exporter.clear()

    await client.get(url)
    await client.get(url)

    spans = [
        span
        for span in capfire.exporter.exported_spans_as_dict()
        if span["name"] == "read_cache.read_through"
    ]
    assert [span["attributes"]["cache.lookup.outcome"] for span in spans] == ["miss", "hit"]
    assert {span["attributes"]["cache.group"] for span in spans} == {"note"}
    assert {span["attributes"]["cache.operation"] for span in spans} == {"entity"}
    # The SDK signals a hit by raising CacheHitException. That is control flow,
    # so a hit span must not record an exception or an error level.
    for span in spans:
        assert not [event for event in span.get("events", []) if event["name"] == "exception"]
        assert span["attributes"].get("logfire.level_num", 9) < 17


@pytest.mark.asyncio
async def test_deleting_a_project_retires_its_cached_reads(
    cached_app: CachedApp, client: AsyncClient, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Rule: a hit is served before the route checks its project, so deletes must retire.

    Otherwise a deleted project's notes stay readable from the cache for the TTL.
    """
    created = await client.post(
        "/v2/projects/",
        json={
            "name": "doomed",
            "path": str(tmp_path_factory.mktemp("doomed")),
            "set_default": False,
        },
    )
    assert created.status_code == 201, created.text
    doomed_id = created.json()["new_project"]["external_id"]
    doomed_url = f"/v2/projects/{doomed_id}"
    note = await _create(client, doomed_url, "Doomed Note")
    note_url = f"{doomed_url}/knowledge/entities/{note['external_id']}"
    await client.get(note_url)
    assert _status(await client.get(note_url)) == "HIT"

    deleted = await client.delete(doomed_url, params={"delete_notes": "true"})
    assert deleted.status_code == 200, deleted.text

    after = await client.get(note_url)
    assert after.status_code == 404
    assert _status(after) != "HIT"


@pytest.mark.asyncio
async def test_query_values_are_keyed_structurally_and_only_when_declared(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: different requests never share an entry, and ignored parameters do not split one.

    An encoded ``&`` inside a value must not collide with a real second parameter,
    and a parameter the route does not read (a cache-busting nonce) must not create
    a separate entry.
    """
    await _create(client, project_url, "Listed")
    list_url = f"{project_url}/directory/list"

    await client.get(list_url, params={"dir_name": "foo&page=2"})
    decoded = await client.get(f"{list_url}?dir_name=foo&page=2")
    assert _status(decoded) == "MISS"

    await client.get(list_url, params={"dir_name": "/", "nonce": "1"})
    other_nonce = await client.get(list_url, params={"dir_name": "/", "nonce": "2"})
    assert _status(other_nonce) == "HIT"


@pytest.mark.asyncio
async def test_query_bodies_are_keyed_by_their_validated_model(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: body fields the model ignores, key order and spelled-out defaults share an entry.

    The search model ignores unknown fields, so a nonce in the body runs the same
    search and must not store a second copy of the response.
    """
    await _create(client, project_url, "Searched", "needle")
    search_url = f"{project_url}/search/"

    first = await _query(client, search_url, {"text": "needle", "nonce": "1"})
    assert _status(first) == "MISS"
    other_nonce = await _query(client, search_url, {"nonce": "2", "text": "needle"})
    assert _status(other_nonce) == "HIT"
    different = await _query(client, search_url, {"text": "haystack"})
    assert _status(different) == "MISS"


@pytest.mark.asyncio
async def test_oversized_responses_are_served_but_not_stored(
    cached_app: CachedApp,
    client: AsyncClient,
    project_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule: responses over the payload cap are answered normally and never cached.

    The app sets the SDK's cap to READ_CACHE_MAX_PAYLOAD_BYTES. Building a real
    multi-megabyte note is slow, so this test lowers the cap (an injected edge case)
    to exercise the same path with a small note.
    """
    redis_fastapi_cache = importlib.import_module("redis_fastapi.cache")

    assert redis_fastapi_cache.MAX_CACHEABLE_BODY_SIZE == READ_CACHE_MAX_PAYLOAD_BYTES

    note = await _create(client, project_url, "Too Big", "x" * 600)
    url = f"{project_url}/knowledge/entities/{note['external_id']}"
    monkeypatch.setattr(redis_fastapi_cache, "MAX_CACHEABLE_BODY_SIZE", 256)

    first = await client.get(url)
    second = await client.get(url)

    assert first.status_code == 200
    assert "x" * 600 in first.json()["content"]
    assert _status(second) != "HIT"
