"""The read-cache rules, end to end through the API against a real Redis.

Each test states one rule from the read-cache design and drives it through real
routes, real writes and real Redis. ``X-Redis-Cache`` (set by fastapi-redis-sdk)
reports whether a response came from the cache.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import pytest
from fastapi import FastAPI
import logfire
from httpx import AsyncClient, Response
from logfire.testing import CaptureLogfire, TestExporter, capfire as capfire, get_collected_metrics
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

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
async def test_a_fuzzy_resolution_is_cached_until_names_change(
    cached_app: CachedApp, client: AsyncClient, project_url: str
) -> None:
    """Rule: a fuzzy match is cached like an exact one and retires when names change.

    A client repeating the same read must not reach the database each time. Creating
    a note retires the entry, so a new exact match is found on the next resolve.
    """
    note = await _create(client, project_url, "Quarterly Planning Notes")
    body = {"identifier": "quarterly planning"}

    first = await _query(client, f"{project_url}/knowledge/resolve", body)
    second = await _query(client, f"{project_url}/knowledge/resolve", body)

    assert first.status_code == 200, first.text
    assert first.json()["resolution_method"] == "search"
    assert _status(second) == "HIT"
    assert second.json()["external_id"] == note["external_id"]

    exact = await _create(client, project_url, "quarterly planning")
    after_create = await _query(client, f"{project_url}/knowledge/resolve", body)

    assert _status(after_create) != "HIT"
    assert after_create.json()["external_id"] == exact["external_id"]


@pytest.mark.parametrize(
    "spelling",
    [
        # The file path without its .md extension.
        "rules/Cache Layer Design",
        # The note's slug under its folder, as clients and memory:// URLs write it.
        "rules/cache-layer-design",
    ],
)
@pytest.mark.asyncio
async def test_an_exact_match_is_cached_whatever_spelling_found_it(
    cached_app: CachedApp, client: AsyncClient, project_url: str, spelling: str
) -> None:
    """Rule: only fuzzy search guesses; an exact lookup under any spelling is cached.

    These spellings never equal the stored title, permalink or file path character
    for character, so the route used to label them a search and leave them uncached,
    and a client reading notes by path reached the database on every read.
    """
    note = await _create(client, project_url, "Cache Layer Design")
    body = {"identifier": spelling}

    first = await _query(client, f"{project_url}/knowledge/resolve", body)
    second = await _query(client, f"{project_url}/knowledge/resolve", body)

    assert first.status_code == 200, first.text
    assert first.json()["external_id"] == note["external_id"]
    assert _status(second) == "HIT"
    assert second.json()["external_id"] == note["external_id"]


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


@dataclass(frozen=True, slots=True)
class Exported:
    """What Logfire exported while configured at one min_level."""

    spans: TestExporter
    metrics: InMemoryMetricReader


def _export_at(min_level: Literal["info", "debug"]) -> Exported:
    spans = TestExporter()
    metrics = InMemoryMetricReader()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        min_level=min_level,
        additional_span_processors=[SimpleSpanProcessor(spans)],
        metrics=logfire.MetricsOptions(additional_readers=[metrics]),
    )
    return Exported(spans=spans, metrics=metrics)


async def _read_twice_instrumented(cached_app: CachedApp, client: AsyncClient, url: str) -> None:
    # The shared app has already served requests, so Starlette has built its
    # middleware stack. Drop it so the next request rebuilds it with (and, after
    # the block, without) the OpenTelemetry middleware.
    cached_app.app.middleware_stack = None
    try:
        with logfire.instrument_fastapi(cached_app.app):
            await client.get(url)
            await client.get(url)
    finally:
        cached_app.app.middleware_stack = None


def _route_spans(exported: Exported, route: str) -> list[dict[str, Any]]:
    return [
        span
        for span in exported.spans.exported_spans_as_dict()
        if span["attributes"].get("http.route") == route
        and span["attributes"].get("logfire.span_type") == "span"
    ]


def _lookup_counts(exported: Exported) -> dict[tuple[str, str, str], int]:
    [lookups] = [
        metric
        for metric in get_collected_metrics(exported.metrics)
        if metric["name"] == "read_cache.lookups"
    ]
    return {
        (
            point["attributes"]["cache.operation"],
            point["attributes"]["cache.group"],
            point["attributes"]["outcome"],
        ): point["value"]
        for point in lookups["data"]["data_points"]
    }


ENTITY_ROUTE = "/v2/projects/{project_id}/knowledge/entities/{entity_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize("min_level", ["info", "debug"])
async def test_each_cached_read_reports_its_outcome_on_the_request_span(
    cached_app: CachedApp,
    client: AsyncClient,
    project_url: str,
    min_level: Literal["info", "debug"],
) -> None:
    """Rule: every cached read is observable on its request span and in a counter.

    Basic Memory Cloud production exports at info, where the read_through step
    span is a no-op, so the outcome must not depend on it.
    """
    note = await _create(client, project_url, "Observed Read")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"
    exported = _export_at(min_level)

    await _read_twice_instrumented(cached_app, client, url)

    request_spans = _route_spans(exported, ENTITY_ROUTE)
    assert [span["attributes"]["cache.lookup.outcome"] for span in request_spans] == [
        "miss",
        "hit",
    ]
    assert {span["attributes"]["cache.group"] for span in request_spans} == {"note"}
    assert {span["attributes"]["cache.operation"] for span in request_spans} == {"entity"}
    assert _lookup_counts(exported) == {("entity", "note", "miss"): 1, ("entity", "note", "hit"): 1}
    # The SDK signals a hit by raising CacheHitException. That is control flow,
    # so a hit must not record an exception or an error level.
    for span in request_spans:
        assert not [event for event in span.get("events", []) if event["name"] == "exception"]
        assert span["attributes"].get("logfire.level_num", 9) < 17

    step_spans = [
        span
        for span in exported.spans.exported_spans_as_dict()
        if span["name"] == "read_cache.read_through"
        and span["attributes"].get("logfire.span_type") == "span"
    ]
    if min_level == "info":
        assert step_spans == []
    else:
        request_ids = {span["context"]["span_id"] for span in request_spans}
        assert len(step_spans) == 2
        assert {span["parent"]["span_id"] for span in step_spans} <= request_ids


@pytest.mark.asyncio
async def test_a_cache_hit_is_not_recorded_as_an_exception_on_the_request_span(
    cached_app: CachedApp, client: AsyncClient, project_url: str, capfire: CaptureLogfire
) -> None:
    """Rule: a hit is a response, not an error, on the route span production records.

    fastapi-redis-sdk signals a hit by raising CacheHitException. Raised while FastAPI
    solves dependencies, the FastAPI instrumentation records it on the request span,
    which in production meant one exception per cache hit.
    """
    note = await _create(client, project_url, "Hit Without Exception")
    url = f"{project_url}/knowledge/entities/{note['external_id']}"

    # The shared app has already served requests, so Starlette has built its
    # middleware stack. Drop it so the next request rebuilds it with (and, after
    # the block, without) the OpenTelemetry middleware.
    cached_app.app.middleware_stack = None
    try:
        with logfire.instrument_fastapi(cached_app.app):
            first = await client.get(url)
            capfire.exporter.clear()
            hit = await client.get(url)
    finally:
        cached_app.app.middleware_stack = None

    assert (_status(first), _status(hit)) == ("MISS", "HIT")
    assert hit.json() == first.json()
    spans = capfire.exporter.exported_spans_as_dict()
    request_spans = [
        span
        for span in spans
        if span["attributes"].get("http.route")
        == "/v2/projects/{project_id}/knowledge/entities/{entity_id}"
    ]
    assert request_spans, [span["name"] for span in spans]
    recorded = [
        event["attributes"].get("exception.type")
        for span in spans
        for event in span.get("events", [])
        if event["name"] == "exception"
    ]
    assert recorded == []


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
