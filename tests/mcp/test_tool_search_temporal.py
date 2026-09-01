"""End-to-end valid-time search through the MCP `search_notes` tool (SPEC-82).

These tests exercise the whole chain the spec's acceptance cases describe: markdown
carrying temporal qualifiers is written through `write_note`, indexed, projected, and
then queried by authored valid time through `search_notes`.

The scenario is the spec's own: one note holding two `[decision]` observations that
disagree about the cache layer, each qualified with the window it was effective over.
Both live in a *single* note on purpose -- that is what makes entity-granular filtering
insufficient and forces the projection to address individual observations.
"""

import inspect
from typing import Any

import pytest

from basic_memory.mcp.tools import write_note
from basic_memory.mcp.tools.search import search_notes

# The spec's worked example, verbatim: one note, two decisions, adjacent half-open
# effective windows meeting at the July 27 cutover.
CACHE_LAYER_NOTE = """\
# Cache Layer

## Observations
- [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
- [decision] @effective[2026-07-27,) The cache layer will use Memcached.
"""

# The same two decisions, written the convenient way. `@effective:2026-07-27` denotes
# `[2026-07-27,)` -- from the cutover onward -- so the cutover answers must not change.
CACHE_LAYER_POINT_NOTE = """\
# Cache Layer

## Observations
- [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
- [decision] @effective:2026-07-27 The cache layer will use Memcached.
"""

UNDATED_NOTE = """\
# Queue Layer

## Observations
- [decision] The queue layer will use RabbitMQ.
"""


async def _write_cache_layer_note(project_name: str) -> None:
    await write_note(
        project=project_name,
        title="Cache Layer",
        directory="decisions",
        content=CACHE_LAYER_NOTE,
    )


def _contents(response: dict[str, Any]) -> list[str]:
    """The matched observation text of every result, for readable assertions."""
    return [result["content"] or "" for result in response["results"]]


@pytest.mark.asyncio
async def test_valid_at_after_cutover_returns_memcached_excludes_redis(client, test_project):
    """Acceptance 5: `valid_at=2026-07-28` returns Memcached and not Redis.

    July 28 falls inside `[2026-07-27,)` and outside `[2026-06-10,2026-07-27)`, whose
    exclusive upper bound expires it exactly at the cutover.
    """
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-28",
        output_format="json",
    )

    assert isinstance(response, dict), response
    contents = _contents(response)
    assert any("Memcached" in content for content in contents), contents
    assert not any("Redis" in content for content in contents), contents


@pytest.mark.asyncio
async def test_valid_at_before_cutover_returns_redis_excludes_memcached(client, test_project):
    """Acceptance 6: `valid_at=2026-07-01` returns Redis and not Memcached."""
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-01",
        output_format="json",
    )

    assert isinstance(response, dict), response
    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert not any("Memcached" in content for content in contents), contents


@pytest.mark.asyncio
async def test_point_qualifier_answers_the_cutover_like_a_range(client, test_project):
    """The convenient form reaches the index and the predicate unchanged.

    `@effective:2026-07-27` means "from the cutover onward", so it must answer the
    spec's two questions exactly as the explicit `[2026-07-27,)` range does -- and it
    must not expire at midnight, which is what a closed single-day range would do.
    """
    await write_note(
        project=test_project.name,
        title="Cache Layer",
        directory="decisions",
        content=CACHE_LAYER_POINT_NOTE,
    )

    after = await search_notes(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-28",
        output_format="json",
    )
    before = await search_notes(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-01",
        output_format="json",
    )

    assert isinstance(after, dict) and isinstance(before, dict)
    after_contents = _contents(after)
    assert any("Memcached" in content for content in after_contents), after_contents
    assert not any("Redis" in content for content in after_contents), after_contents

    before_contents = _contents(before)
    assert any("Redis" in content for content in before_contents), before_contents
    assert not any("Memcached" in content for content in before_contents), before_contents


@pytest.mark.asyncio
async def test_no_temporal_filter_lets_both_decisions_compete(client, test_project):
    """Acceptance 7: with no valid-time filter both decisions are candidates again."""
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        query="cache layer",
        entity_types=["observation"],
        output_format="json",
    )

    assert isinstance(response, dict), response
    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents
    # Ranking, not filtering, decides between them -- and nothing claims a filter ran.
    assert response.get("temporal_applied") is None


@pytest.mark.asyncio
async def test_valid_overlaps_returns_both_decisions(client, test_project):
    """A window spanning the cutover overlaps both effective ranges."""
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_overlaps="[2026-06-01,2026-08-01)",
        output_format="json",
    )

    assert isinstance(response, dict), response
    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents


@pytest.mark.asyncio
async def test_undated_note_search_is_unchanged(client, test_project):
    """Acceptance 1: a note with no qualifier searches exactly as it always did."""
    await write_note(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    response = await search_notes(
        project=test_project.name,
        query="RabbitMQ",
        output_format="json",
    )

    assert isinstance(response, dict), response
    assert response["results"], response
    assert response.get("temporal_applied") is None


@pytest.mark.asyncio
async def test_valid_at_excludes_undated_observations(client, test_project):
    """Acceptance 8: an undated statement cannot answer "what was true then"."""
    await _write_cache_layer_note(test_project.name)
    await write_note(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    unfiltered = await search_notes(
        project=test_project.name,
        query="layer",
        entity_types=["observation"],
        output_format="json",
    )
    assert isinstance(unfiltered, dict), unfiltered
    assert any("RabbitMQ" in content for content in _contents(unfiltered))

    filtered = await search_notes(
        project=test_project.name,
        query="layer",
        valid_at="2026-07-28",
        output_format="json",
    )
    assert isinstance(filtered, dict), filtered
    assert not any("RabbitMQ" in content for content in _contents(filtered))
    assert filtered["temporal_applied"] is True


@pytest.mark.asyncio
async def test_results_carry_the_assertion_that_matched(client, test_project):
    """A valid-time hit explains itself: kind, canonical range, and authored text."""
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        query="cache layer",
        valid_at="2026-07-28",
        output_format="json",
    )

    assert isinstance(response, dict), response
    [result] = [r for r in response["results"] if "Memcached" in (r["content"] or "")]
    [assertion] = result["temporal"]
    assert assertion["kind"] == "effective"
    assert assertion["source_text"] == "@effective[2026-07-27,)"
    assert assertion["valid_during"]["literal"] == "[2026-07-27,)"
    assert assertion["valid_during"]["axis"] == "date"
    assert assertion["valid_during"]["lower"] == "2026-07-27"
    assert assertion["valid_during"]["lower_inclusive"] is True
    # JSON output drops null fields, so an unbounded end shows up as an absent key.
    assert assertion["valid_during"].get("upper") is None


@pytest.mark.asyncio
async def test_markdown_output_labels_the_time_kind(client, test_project):
    """Human-readable output names the kind instead of printing a bare date."""
    await _write_cache_layer_note(test_project.name)

    rendered = await search_notes(
        project=test_project.name,
        query="cache layer",
        valid_at="2026-07-28",
    )

    assert isinstance(rendered, str), rendered
    assert "effective valid time: [2026-07-27,) (date)" in rendered


@pytest.mark.asyncio
async def test_kind_only_filter_finds_every_source_of_that_kind(client, test_project):
    """A kind with no point or range is a legal question: who asserts this kind?"""
    await _write_cache_layer_note(test_project.name)
    await write_note(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    response = await search_notes(
        project=test_project.name,
        query="layer",
        time_kind="effective",
        output_format="json",
    )

    assert isinstance(response, dict), response
    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents
    assert not any("RabbitMQ" in content for content in contents), contents


@pytest.mark.asyncio
async def test_valid_at_and_valid_overlaps_together_are_refused(client, test_project):
    """The two forms ask different questions; supplying both is an authoring error."""
    with pytest.raises(ValueError, match="not both"):
        await search_notes(
            project=test_project.name,
            query="cache layer",
            valid_at="2026-07-28",
            valid_overlaps="[2026-06-01,2026-08-01)",
        )


@pytest.mark.asyncio
async def test_a_malformed_valid_time_filter_is_refused_rather_than_searched(client, test_project):
    """A typo in a valid-time filter is an error, not a search that finds nothing."""
    await _write_cache_layer_note(test_project.name)

    with pytest.raises(ValueError, match="2026-13-01"):
        await search_notes(
            project=test_project.name,
            query="cache layer",
            valid_at="2026-13-01",
            output_format="json",
        )


@pytest.mark.asyncio
async def test_all_projects_search_refuses_a_malformed_filter_instead_of_reporting_nothing(
    client, test_project
):
    """The same typo across every project must not come back as "no matches found".

    Through the real API each per-project leg 400s on the bad bound and returns a
    `# Search Failed` string, which the fan-out logs and skips as an unavailable project.
    Skipping every project leaves an empty response that still claims the filter ran --
    an invalid query wearing the shape of a successful one.
    """
    await _write_cache_layer_note(test_project.name)

    with pytest.raises(ValueError, match="2026-13-01"):
        await search_notes(
            query="cache layer",
            search_all_projects=True,
            valid_at="2026-13-01",
            output_format="json",
        )


@pytest.mark.asyncio
async def test_time_kind_alone_is_enough_search_criteria(client, test_project):
    """A valid-time filter is real criteria, so it must not trip the empty-query guard."""
    await _write_cache_layer_note(test_project.name)

    response = await search_notes(
        project=test_project.name,
        time_kind="effective",
        output_format="json",
    )

    assert isinstance(response, dict), response
    assert len(response["results"]) == 2


def test_tool_help_documents_undated_exclusion():
    """Acceptance 8: the exclusion is documented where a caller will read it."""
    doc = inspect.getdoc(search_notes) or ""
    assert "Sources with no temporal qualifier are excluded" in doc
    assert "valid_at" in doc and "valid_overlaps" in doc and "time_kind" in doc
