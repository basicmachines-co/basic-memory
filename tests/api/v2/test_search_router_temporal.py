"""Valid-time filters over the v2 search endpoint (SPEC-82).

The router is where three things have to line up: the filter reaches the service, the
matched assertions come back with the results, and the response says the filter actually
ran. That last one is not decoration -- `SearchQuery` ignores unknown fields, so without
an explicit confirmation an older server would answer a valid-time query with unfiltered
results that look filtered.
"""

from textwrap import dedent
from typing import Any

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas import Entity as EntitySchema

CACHE_LAYER_MARKDOWN = dedent("""
    # Cache Layer

    ## Observations
    - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
    - [decision] @effective[2026-07-27,) The cache layer will use Memcached.
    """)

UNDATED_MARKDOWN = dedent("""
    # Queue Layer

    ## Observations
    - [decision] The queue layer will use RabbitMQ.
    """)


async def _index_note(entity_service, search_service, title: str, content: str):
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title=title,
            note_type="note",
            directory="decisions",
            content=content,
        )
    )
    await search_service.index_entity(entity)
    return entity


async def _search(client: AsyncClient, v2_project_url: str, **query: Any) -> dict[str, Any]:
    response = await client.post(f"{v2_project_url}/search/", json=query)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_temporal_filter_round_trips_through_v2_search(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """A valid-time query narrows to the observation in force and explains why."""
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    payload = await _search(
        client,
        v2_project_url,
        text="cache layer",
        entity_types=["observation"],
        time_role="effective",
        valid_at="2026-07-28",
    )

    assert payload["temporal_applied"] is True
    contents = [result["content"] for result in payload["results"]]
    assert any("Memcached" in (content or "") for content in contents), contents
    assert not any("Redis" in (content or "") for content in contents), contents

    [result] = payload["results"]
    [assertion] = result["temporal"]
    assert assertion["role"] == "effective"
    assert assertion["source_text"] == "@effective[2026-07-27,)"
    assert assertion["valid_during"] == {
        "kind": "date",
        "literal": "[2026-07-27,)",
        "lower": "2026-07-27",
        "upper": None,
        "lower_inclusive": True,
        "upper_inclusive": False,
        "is_empty": False,
    }


@pytest.mark.asyncio
async def test_overlap_filter_returns_both_competing_decisions(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """A window spanning the cutover overlaps both effective periods."""
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    payload = await _search(
        client,
        v2_project_url,
        text="cache layer",
        entity_types=["observation"],
        valid_overlaps="[2026-06-01,2026-08-01)",
    )

    assert payload["temporal_applied"] is True
    contents = " ".join(result["content"] or "" for result in payload["results"])
    assert "Redis" in contents and "Memcached" in contents


@pytest.mark.asyncio
async def test_search_without_a_temporal_filter_is_unchanged(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """An ordinary search payload is byte-for-byte what it was before valid time.

    `temporal_applied` stays null rather than false, and no result carries a temporal
    block, so nothing about an existing client's parsing changes.
    """
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    payload = await _search(
        client, v2_project_url, text="cache layer", entity_types=["observation"]
    )

    assert payload["temporal_applied"] is None
    assert payload["results"]
    assert all(result["temporal"] is None for result in payload["results"])


@pytest.mark.asyncio
async def test_undated_note_is_excluded_and_the_exclusion_is_confirmed(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Acceptance 8 over HTTP: undated sources drop out, and the server says so."""
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)
    await _index_note(entity_service, search_service, "Queue Layer", UNDATED_MARKDOWN)

    unfiltered = await _search(client, v2_project_url, text="layer", entity_types=["observation"])
    assert any("RabbitMQ" in (r["content"] or "") for r in unfiltered["results"])

    filtered = await _search(
        client,
        v2_project_url,
        text="layer",
        entity_types=["observation"],
        valid_at="2026-07-28",
    )
    assert filtered["temporal_applied"] is True
    assert not any("RabbitMQ" in (r["content"] or "") for r in filtered["results"])


@pytest.mark.asyncio
async def test_pagination_totals_respect_the_temporal_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """`total` comes from a separate count query; it must run the same predicate.

    The router derives `has_more` from that total, so a count that ignored valid time
    would advertise pages that do not exist.
    """
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)
    await _index_note(entity_service, search_service, "Queue Layer", UNDATED_MARKDOWN)

    payload = await _search(
        client,
        v2_project_url,
        text="layer",
        entity_types=["observation"],
        valid_at="2026-07-28",
    )

    assert payload["total"] == len(payload["results"]) == 1
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_a_valid_time_query_with_no_matches_still_confirms_the_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """An empty answer to a valid-time question is different from an unfiltered one.

    Nothing was in force in 2020, so there is nothing to hydrate -- but the caller still
    needs to know the filter ran, or it cannot tell this apart from a stale server.
    """
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    payload = await _search(
        client,
        v2_project_url,
        text="cache layer",
        entity_types=["observation"],
        valid_at="2020-01-01",
    )

    assert payload["results"] == []
    assert payload["total"] == 0
    assert payload["temporal_applied"] is True


@pytest.mark.asyncio
async def test_valid_at_and_valid_overlaps_together_are_rejected(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """The schema refuses the contradictory pair, so it never reaches the service."""
    response = await client.post(
        f"{v2_project_url}/search/",
        json={"text": "cache", "valid_at": "2026-07-28", "valid_overlaps": "[2026-06-10,)"},
    )

    assert response.status_code == 422
    assert "not both" in response.text


@pytest.mark.asyncio
async def test_temporal_only_query_is_accepted_as_criteria(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """A valid-time filter alone is a complete search request."""
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    payload = await _search(
        client, v2_project_url, entity_types=["observation"], time_role="effective"
    )

    assert payload["temporal_applied"] is True
    assert len(payload["results"]) == 2


@pytest.mark.asyncio
async def test_read_cache_distinguishes_two_valid_time_questions(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """The response cache keys on the whole query, so two dates cannot share an entry.

    The digest hashes `SearchQuery.model_dump()`, which now includes the valid-time
    fields; without that, the second question would be answered with the first's cached
    results.
    """
    await _index_note(entity_service, search_service, "Cache Layer", CACHE_LAYER_MARKDOWN)

    after = await _search(
        client,
        v2_project_url,
        text="cache layer",
        entity_types=["observation"],
        valid_at="2026-07-28",
    )
    before = await _search(
        client,
        v2_project_url,
        text="cache layer",
        entity_types=["observation"],
        valid_at="2026-07-01",
    )

    assert "Memcached" in (after["results"][0]["content"] or "")
    assert "Redis" in (before["results"][0]["content"] or "")
