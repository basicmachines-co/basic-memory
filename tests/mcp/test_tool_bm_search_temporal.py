"""End-to-end valid-time search through the MCP `bm_search` tool (SPEC-82).

These tests exercise the whole chain the spec's acceptance cases describe: markdown
carrying temporal qualifiers is written through `bm_write`, indexed, projected, and
then queried by authored valid time through `bm_search`.

The scenario is the spec's own: one note holding two `[decision]` observations that
disagree about the cache layer, each qualified with the window it was effective over.
Both live in a *single* note on purpose -- that is what makes entity-granular filtering
insufficient and forces the projection to address individual observations.
"""

import importlib
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_search, bm_write

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
    await bm_write(
        project=project_name,
        title="Cache Layer",
        directory="decisions",
        content=CACHE_LAYER_NOTE,
    )


async def search_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_search(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


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

    response = await search_json(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-28",
    )

    contents = _contents(response)
    assert any("Memcached" in content for content in contents), contents
    assert not any("Redis" in content for content in contents), contents


@pytest.mark.asyncio
async def test_valid_at_before_cutover_returns_redis_excludes_memcached(client, test_project):
    """Acceptance 6: `valid_at=2026-07-01` returns Redis and not Memcached."""
    await _write_cache_layer_note(test_project.name)

    response = await search_json(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-01",
    )

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
    await bm_write(
        project=test_project.name,
        title="Cache Layer",
        directory="decisions",
        content=CACHE_LAYER_POINT_NOTE,
    )

    after = await search_json(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-28",
    )
    before = await search_json(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_at="2026-07-01",
    )

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

    response = await search_json(
        project=test_project.name,
        query="cache layer",
        items=["observation"],
    )

    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents
    # Ranking, not filtering, decides between them -- and nothing claims a filter ran.
    assert response.get("temporal_applied") is None


@pytest.mark.asyncio
async def test_valid_overlaps_returns_both_decisions(client, test_project):
    """A window spanning the cutover overlaps both effective ranges."""
    await _write_cache_layer_note(test_project.name)

    response = await search_json(
        project=test_project.name,
        query="cache layer",
        time_kind="effective",
        valid_overlaps="[2026-06-01,2026-08-01)",
    )

    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents


@pytest.mark.asyncio
async def test_undated_note_search_is_unchanged(client, test_project):
    """Acceptance 1: a note with no qualifier searches exactly as it always did."""
    await bm_write(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    response = await search_json(
        project=test_project.name,
        query="RabbitMQ",
    )

    assert response["results"], response
    assert response.get("temporal_applied") is None


@pytest.mark.asyncio
async def test_valid_at_excludes_undated_observations(client, test_project):
    """Acceptance 8: an undated statement cannot answer "what was true then"."""
    await _write_cache_layer_note(test_project.name)
    await bm_write(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    unfiltered = await search_json(
        project=test_project.name,
        query="layer",
        items=["observation"],
    )
    assert any("RabbitMQ" in content for content in _contents(unfiltered))

    filtered = await search_json(
        project=test_project.name,
        query="layer",
        valid_at="2026-07-28",
    )
    assert not any("RabbitMQ" in content for content in _contents(filtered))
    assert filtered["temporal_applied"] is True


@pytest.mark.asyncio
async def test_results_carry_the_assertion_that_matched(client, test_project):
    """A valid-time hit explains itself: kind, canonical range, and authored text."""
    await _write_cache_layer_note(test_project.name)

    response = await search_json(
        project=test_project.name,
        query="cache layer",
        valid_at="2026-07-28",
    )

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
async def test_kind_only_filter_finds_every_source_of_that_kind(client, test_project):
    """A kind with no point or range is a legal question: who asserts this kind?"""
    await _write_cache_layer_note(test_project.name)
    await bm_write(
        project=test_project.name,
        title="Queue Layer",
        directory="decisions",
        content=UNDATED_NOTE,
    )

    response = await search_json(
        project=test_project.name,
        query="layer",
        time_kind="effective",
    )

    contents = _contents(response)
    assert any("Redis" in content for content in contents), contents
    assert any("Memcached" in content for content in contents), contents
    assert not any("RabbitMQ" in content for content in contents), contents


@pytest.mark.asyncio
async def test_valid_at_and_valid_overlaps_together_are_refused(client, test_project):
    """The two forms ask different questions; supplying both is an authoring error."""
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: .*not both"):
        await bm_search(
            project=test_project.name,
            query="cache layer",
            valid_at="2026-07-28",
            valid_overlaps="[2026-06-01,2026-08-01)",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["project", "all_projects"])
async def test_a_malformed_valid_time_filter_is_refused_rather_than_searched(
    client, test_project, scope
):
    """A typo in a valid-time filter is an error, not a search that finds nothing.

    Across projects it matters most: each database would 400 on the bad bound, the
    fan-out would skip every one, and the empty merge would still claim the filter ran.
    """
    await _write_cache_layer_note(test_project.name)
    where: dict[str, Any] = (
        {"project": test_project.name} if scope == "project" else {"all_projects": True}
    )

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: .*2026-13-01"):
        await bm_search(query="cache layer", valid_at="2026-13-01", **where)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("valid_at", ""), ("valid_overlaps", "   "), ("time_kind", "")],
)
async def test_all_projects_search_refuses_an_empty_filter_instead_of_running_unfiltered(
    client, test_project, field: str, value: str
):
    """An empty value must not resurrect the silent-unfiltered fan-out.

    A blank field read as absence meant every per-project leg ran an ordinary
    unfiltered search, so the merged answer came back full of undated rows with
    nothing marking it as unfiltered. Refused once, before any project is searched,
    by the parser the per-project legs share.
    """
    await _write_cache_layer_note(test_project.name)
    blank_filter: dict[str, Any] = {field: value}

    with pytest.raises(ToolError, match=f"{field} was given as an empty value"):
        await bm_search(query="cache layer", all_projects=True, **blank_filter)


@pytest.mark.asyncio
async def test_time_kind_alone_is_enough_search_criteria(client, test_project):
    """A valid-time filter is real criteria, so it must not trip the empty-query guard."""
    await _write_cache_layer_note(test_project.name)

    response = await search_json(
        project=test_project.name,
        time_kind="effective",
    )

    assert len(response["results"]) == 2


# --- The manual must not promise what the reader refuses ---


def _search_manual() -> str:
    from basic_memory.man import MAN_DIR

    return (MAN_DIR / "man3" / "bm-search(3).md").read_text(encoding="utf-8")


def test_the_man_page_documents_undated_exclusion():
    """Acceptance 8: the exclusion is documented where a caller will read it."""
    page = _search_manual()

    assert "A valid-time filter excludes every source without a temporal qualifier" in page
    assert "valid_at" in page and "valid_overlaps" in page and "time_kind" in page


def test_the_man_page_does_not_advertise_relative_qualifiers():
    """The manual is what an agent reads before writing a qualifier.

    A stale promise here is worse than a stale comment: the agent writes
    `@occurred:yesterday`, the reader silently declines it, and every valid-time
    search quietly omits the line. The behaviour is pinned in tests/test_temporal.py.
    """
    page = _search_manual()

    assert "A relative date is never filed as a qualifier" in page
    # The quoted-form example list must not offer one as a spelling that files.
    assert '`@occurred:"2 days ago"` and `@"June 2026"` all file' not in page


@pytest.mark.asyncio
async def test_all_projects_search_propagates_a_filter_no_project_could_apply(
    client, test_project, monkeypatch
):
    """Every leg failing must not read as a successful search that found nothing.

    A server predating SPEC-82 accepts a valid-time query and answers unfiltered, so
    `SearchClient` refuses any response that does not confirm `temporal_applied`. The
    fan-out catches that refusal, logs the project and skips it -- correct for one
    unavailable project, and wrong for a whole fleet of old servers, because the merged
    answer then reported `temporal_applied: true` over zero results. The caller could not
    tell "no note asserts that window" from "the filter ran nowhere", which is the exact
    confusion the confirmation field exists to prevent.
    """
    await _write_cache_layer_note(test_project.name)

    # Trigger: a server predating SPEC-82 answers a valid-time query unfiltered.
    # Why: no such server exists in-process; the client's refusal of its response is
    #      the one fact injected here. Project discovery and the fan-out run for real.
    # Outcome: the one database is skipped, exactly as an old server would be.
    class RefusingScopedSearchClient:
        def __init__(self, client: Any) -> None:
            pass

        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise ValueError("The search API did not apply the requested valid-time filter")

    monkeypatch.setattr(
        importlib.import_module("basic_memory.mcp.clients"),
        "ScopedSearchClient",
        RefusingScopedSearchClient,
    )

    with pytest.raises(ValueError, match="No project applied the requested valid-time filter"):
        await bm_search(query="cache layer", mode="text", all_projects=True, valid_at="2026-07-01")
