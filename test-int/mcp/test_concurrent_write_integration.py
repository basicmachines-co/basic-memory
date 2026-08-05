"""Integration tests for CONCURRENT write_note MCP operations.

The write path is guarded by a FileService semaphore and race handling in the
entity_service, but the suite previously had no integration coverage that
actually drives multiple writes at once through the full stack
(MCP Client -> MCP Server -> FastAPI -> Database). These tests exercise that
concurrency to prove writes do not clobber each other, permalinks stay unique,
the search index stays consistent, and reads remain coherent while writes are
in flight. Concurrency matters here because real clients (multiple agents,
watch-driven syncs) can issue overlapping writes, and a lost update or a
corrupted index would silently drop knowledge.
"""

import asyncio
import json

import pytest
from fastmcp import Client

# Every test in this module shares one session-scoped event loop.
#
# Trigger: pytest-asyncio strict mode defaults to a fresh, function-scoped loop
# per ``@pytest.mark.asyncio`` test (pyproject ``asyncio_default_fixture_loop_scope
# = "function"``), but the MCP local-ASGI path caches its "prepare"
# ``asyncio.Lock`` in a module-level dict keyed by the single global FastAPI app
# (``async_client._prepared_local_asgi_database_prepare_locks``).
# Why: that lock binds to whichever loop first acquires it; on a new
# function-scoped loop the next test's first request hits
# ``RuntimeError: <Lock ...> is bound to a different event loop``.
# Outcome: pinning every test to ``loop_scope="session"`` keeps the cached lock
# on one loop for the whole module, matching the session-scoped conftest
# fixtures (e.g. ``postgres_engine``).


@pytest.mark.asyncio(loop_scope="session")
async def test_write_same_title_same_directory_collision(mcp_server, app, test_project) -> None:
    """Two writes with the same title + directory deterministically collide.

    Same title + same directory normalize to the same permalink. With the
    default ``overwrite=False``, the second write does NOT clobber the first:
    write_note detects the existing note and returns a structured ``conflict``
    result. This drives the collision path deterministically (sequential, no
    race timing) and asserts on the JSON response to pin the observed behavior.
    """

    async with Client(mcp_server) as client:
        first = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Collision Note",
                "directory": "collision",
                "content": "# Collision Note\n\nFirst body.",
                "output_format": "json",
            },
        )
        second = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Collision Note",
                "directory": "collision",
                "content": "# Collision Note\n\nSecond body loses.",
                "output_format": "json",
            },
        )

        first_payload = json.loads(first.content[0].text)
        second_payload = json.loads(second.content[0].text)

        # First write creates the note; the colliding second write is blocked as
        # a conflict (overwrite disabled by default) rather than forking a new
        # permalink or silently overwriting.
        assert first_payload["action"] == "created"
        assert second_payload["action"] == "conflict"
        assert second_payload["error"] == "NOTE_ALREADY_EXISTS"
        # Both writes normalize to the same permalink (project prefix aside).
        assert first_payload["permalink"].endswith("collision/collision-note")
        assert second_payload["permalink"].endswith("collision/collision-note")

        # The first write wins: reading back returns the original body, proving
        # the conflicting write left the committed note untouched.
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Collision Note",
            },
        )
        read_text = read_result.content[0].text
        assert "First body." in read_text
        assert "Second body loses." not in read_text


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_write_different_notes(mcp_server, app, test_project) -> None:
    """Concurrent writes to distinct titles/folders all succeed and read back.

    Fires many write_note calls in parallel across different directories and
    verifies every note is created and independently readable with its own
    content, proving concurrent writes to different entities do not interfere.
    """

    note_count = 10

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Different Note {index}",
                    "directory": f"folder-{index}",
                    "content": f"# Different Note {index}\n\nUnique body {index}.",
                    "tags": f"concurrent,note{index}",
                },
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))

        for index, result in enumerate(results):
            text = result.content[0].text
            assert "# Created note" in text, f"note {index} was not created: {text}"
            assert (
                f"permalink: {test_project.name}/folder-{index}/different-note-{index}" in text
            ), f"note {index} has unexpected permalink: {text}"

        # Every note must be independently readable with its own content.
        async def read_one(index: int):
            return await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"Different Note {index}",
                },
            )

        read_results = await asyncio.gather(*(read_one(i) for i in range(note_count)))
        for index, read_result in enumerate(read_results):
            read_text = read_result.content[0].text
            assert f"Unique body {index}" in read_text, (
                f"note {index} content missing on read: {read_text}"
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_write_same_directory(mcp_server, app, test_project) -> None:
    """Concurrent writes into the SAME directory produce distinct permalinks.

    Writing many notes into one folder at once stresses shared-directory
    creation; each note must exist with a unique permalink and no conflicts.
    """

    note_count = 12
    directory = "shared"

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Shared Dir Note {index}",
                    "directory": directory,
                    "content": f"# Shared Dir Note {index}\n\nEntry number {index}.",
                },
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))

        permalinks: set[str] = set()
        for index, result in enumerate(results):
            text = result.content[0].text
            assert "# Created note" in text, f"note {index} was not created: {text}"
            expected = f"{test_project.name}/{directory}/shared-dir-note-{index}"
            assert f"permalink: {expected}" in text, (
                f"note {index} missing expected permalink {expected}: {text}"
            )
            permalinks.add(expected)

        assert len(permalinks) == note_count, "expected one unique permalink per concurrent note"


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_write_then_search(mcp_server, app, test_project) -> None:
    """After concurrent writes, each note is findable via search.

    Concurrent index updates are a classic race; this writes N notes in parallel
    then searches for each unique token to confirm the FTS index absorbed every
    write without dropping entries.
    """

    note_count = 8

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Searchable Note {index}",
                    "directory": "searchable",
                    "content": (
                        f"# Searchable Note {index}\n\n"
                        f"Contains unique token zylophon{index} for lookup."
                    ),
                },
            )

        await asyncio.gather(*(write_one(i) for i in range(note_count)))

        # Search each unique token; a lost index update would drop a note here.
        async def search_one(index: int):
            return await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": f"zylophon{index}",
                },
            )

        search_results = await asyncio.gather(*(search_one(i) for i in range(note_count)))
        for index, search_result in enumerate(search_results):
            text = search_result.content[0].text
            assert f"Searchable Note {index}" in text, (
                f"note {index} not found in search index: {text}"
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_write_and_read(mcp_server, app, test_project) -> None:
    """Reads of a stable note stay consistent while other writes are in flight.

    Writes an anchor note, then concurrently writes more notes while repeatedly
    reading the anchor. The anchor read must always return its original content,
    proving concurrent writes never corrupt an unrelated, already-committed note.
    """

    async with Client(mcp_server) as client:
        anchor_body = "Anchor content that must never change."
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Anchor Note",
                "directory": "anchor",
                "content": f"# Anchor Note\n\n{anchor_body}",
            },
        )

        async def write_extra(index: int):
            return await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Extra Note {index}",
                    "directory": "extra",
                    "content": f"# Extra Note {index}\n\nExtra body {index}.",
                },
            )

        async def read_anchor():
            return await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": "Anchor Note",
                },
            )

        tasks = [write_extra(i) for i in range(6)] + [read_anchor() for _ in range(6)]
        results = await asyncio.gather(*tasks)

        # The last 6 results are the anchor reads; each must be consistent.
        for read_result in results[6:]:
            text = read_result.content[0].text
            assert anchor_body in text, f"anchor read returned inconsistent content: {text}"


@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_write_high_volume(mcp_server, app, test_project) -> None:
    """Stress: 20+ concurrent writes all succeed with correct content.

    High-volume concurrency maximizes contention on the FileService semaphore
    and DB write path; every note must be created and read back with its own
    body to confirm no writes are lost or interleaved under load.
    """

    note_count = 20

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Volume Note {index}",
                    "directory": "volume",
                    "content": f"# Volume Note {index}\n\nVolume body {index}.",
                },
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))
        for index, result in enumerate(results):
            text = result.content[0].text
            assert "# Created note" in text, f"note {index} was not created under load: {text}"

        async def read_one(index: int):
            return await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"Volume Note {index}",
                },
            )

        read_results = await asyncio.gather(*(read_one(i) for i in range(note_count)))
        for index, read_result in enumerate(read_results):
            text = read_result.content[0].text
            assert f"Volume body {index}" in text, (
                f"note {index} content missing under load: {text}"
            )
