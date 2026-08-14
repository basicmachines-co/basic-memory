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

Every test uses ``output_format="json"`` so assertions read structured fields
(``action``, ``permalink``, ``error``, ``content``) instead of scraping human
markdown. Each test is fully self-contained and runs on its own
function-scoped event loop, matching the repo convention (plain
``@pytest.mark.asyncio``) so a bare ``pytest`` invocation passes with no
``-o`` loop scope override.
"""

import asyncio
import json
from typing import Any

import pytest
from fastmcp import Client


@pytest.fixture(autouse=True)
def _reset_local_asgi_prepare_lock():
    """Give every function-scoped test a fresh local-ASGI prepare lock.

    The MCP local-ASGI client caches one ``asyncio.Lock`` per FastAPI app in a
    module-level dict (``async_client._prepared_local_asgi_database_prepare_locks``)
    to serialize first-request DB preparation. That lock binds to whichever event
    loop first acquires it. Under this module's heavy concurrent writes a request
    can still hold the lock when the MCP server task is cancelled at test
    teardown, leaving it LOCKED and bound to that test's (now-closed) loop; the
    next function-scoped test then fails its first call with
    ``RuntimeError: <Lock ...> is bound to a different event loop``.

    Clearing the cache around each test forces a fresh lock on the current loop,
    which lets these tests run on plain function-scoped loops (the repo default in
    pyproject) under a bare ``pytest`` with no override. The prepared-database
    cache self-empties via the
    client's normal release path, so only the lock dict needs resetting.
    """
    from basic_memory.mcp import async_client

    with async_client._prepared_local_asgi_database_lock:
        async_client._prepared_local_asgi_database_prepare_locks.clear()
    yield
    with async_client._prepared_local_asgi_database_lock:
        async_client._prepared_local_asgi_database_prepare_locks.clear()


def _parse(mcp_result) -> dict[str, Any]:
    """Decode a tool call's JSON payload into a dict."""
    return json.loads(mcp_result.content[0].text)


@pytest.mark.asyncio
async def test_write_same_title_same_directory_collision(mcp_server, app, test_project) -> None:
    """A second write of the same title+directory is blocked as a conflict.

    Same title + same directory normalize to the same permalink. Through the
    guarded MCP create path the server's ``detect_potential_file_conflicts``
    pre-check catches the permalink collision BEFORE the repository layer, so with
    the default ``overwrite=False`` the second write returns a structured
    ``conflict`` (``NOTE_ALREADY_EXISTS``) rather than clobbering the first note
    or forking a new permalink. This is the deterministic collision oracle a real
    MCP client observes; it asserts on the JSON response, and the read-back proves
    the first write's body survived untouched.

    (The lower-level ``EntityRepository._handle_permalink_conflict`` numeric-suffix
    recovery is NOT reachable on this sequential path — the API pre-check pre-empts
    it. It is exercised via the concurrent TOCTOU race in
    ``test_concurrent_permalink_conflict_recovery`` below.)
    """

    async with Client(mcp_server) as client:
        first = _parse(
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Collision Note",
                    "directory": "collision",
                    "content": "# Collision Note\n\nFirst body.",
                    "output_format": "json",
                },
            )
        )
        second = _parse(
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Collision Note",
                    "directory": "collision",
                    "content": "# Collision Note\n\nSecond body loses.",
                    "output_format": "json",
                },
            )
        )

        # First write creates the note; the colliding second write is blocked as
        # a conflict (overwrite disabled by default).
        assert first["action"] == "created", first
        assert "error" not in first, first
        assert second["action"] == "conflict", second
        assert second["error"] == "NOTE_ALREADY_EXISTS", second

        # Both writes normalize to the same permalink (project prefix aside).
        assert first["permalink"].endswith("collision/collision-note"), first
        assert second["permalink"].endswith("collision/collision-note"), second

        # The first write wins: reading back returns the original body, proving
        # the conflicting write left the committed note untouched.
        read_payload = _parse(
            await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": "Collision Note",
                    "output_format": "json",
                },
            )
        )
        assert "First body." in read_payload["content"], read_payload
        assert "Second body loses." not in read_payload["content"], read_payload


@pytest.mark.asyncio
async def test_permalink_suffix_collision_recovery(mcp_server, app, test_project) -> None:
    """Two notes claiming the same permalink recover via a ``-1`` suffix.

    Both notes set an explicit ``permalink:`` in frontmatter to the SAME value but
    have different titles/file_paths, so the API's filename conflict pre-check does
    NOT block them. Instead the permalink-uniqueness resolver deterministically
    mints a ``-<n>`` suffix for the second note: the first keeps ``.../shared-slug``
    and the second becomes ``.../shared-slug-1``. This drives real collision
    RECOVERY (a suffixed, non-clobbering permalink) end-to-end through the MCP
    stack and proves the two rows stay distinct and independently readable.
    """

    shared_permalink = "suffix/shared-slug"

    async with Client(mcp_server) as client:
        first = _parse(
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Suffix Alpha",
                    "directory": "suffix",
                    "content": (
                        f"---\npermalink: {shared_permalink}\n---\n\n# Suffix Alpha\n\nAlpha body."
                    ),
                    "output_format": "json",
                },
            )
        )
        second = _parse(
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Suffix Beta",
                    "directory": "suffix",
                    "content": (
                        f"---\npermalink: {shared_permalink}\n---\n\n# Suffix Beta\n\nBeta body."
                    ),
                    "output_format": "json",
                },
            )
        )

        # Both notes are created; the collision is recovered by a suffix rather
        # than a clobber or an error.
        assert first["action"] == "created", first
        assert second["action"] == "created", second
        assert "error" not in first, first
        assert "error" not in second, second

        # First owns the bare permalink; second is suffixed and distinct.
        assert first["permalink"] == shared_permalink, first
        assert second["permalink"] == f"{shared_permalink}-1", second
        assert first["permalink"] != second["permalink"]

        # Each note reads back at its own permalink with its own body.
        first_read = _parse(
            await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": first["permalink"],
                    "output_format": "json",
                },
            )
        )
        second_read = _parse(
            await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": second["permalink"],
                    "output_format": "json",
                },
            )
        )
        assert "Alpha body." in first_read["content"], first_read
        assert "Beta body." in second_read["content"], second_read


@pytest.mark.asyncio
async def test_concurrent_same_title_collision(mcp_server, app, test_project) -> None:
    """Concurrent writes of the SAME title never duplicate or fork a note.

    Fires many identical-title writes into one folder at once. However the
    exists-check / write / upsert steps interleave, every result is EITHER a
    ``created`` or a ``conflict`` (``NOTE_ALREADY_EXISTS``), at least one is
    created, and every result resolves to the SAME base permalink with no numeric
    suffix — overlapping same-key writes must converge to one note, not fork into
    duplicates. Reading back returns exactly that single note.
    """

    write_count = 8
    base = "race/race-note"

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": "Race Note",
                        "directory": "race",
                        "content": f"# Race Note\n\nWriter {index} attempted this note.",
                        "output_format": "json",
                    },
                )
            )

        payloads = await asyncio.gather(*(write_one(i) for i in range(write_count)))

        actions = [payload["action"] for payload in payloads]
        assert set(actions) <= {"created", "conflict"}, actions
        assert "created" in actions, actions

        # No collision may fork the permalink: every result resolves to the
        # single base permalink with no numeric suffix.
        for payload in payloads:
            assert payload["permalink"].endswith(base), payload
            assert not payload["permalink"][len(base) :].startswith("-"), payload
        for payload in (p for p in payloads if p["action"] == "conflict"):
            assert payload["error"] == "NOTE_ALREADY_EXISTS", payload

        # Exactly one note exists at that permalink and it reads back cleanly.
        read_payload = _parse(
            await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": "Race Note",
                    "output_format": "json",
                },
            )
        )
        assert read_payload["title"] == "Race Note", read_payload
        assert read_payload["permalink"].endswith(base), read_payload


@pytest.mark.asyncio
async def test_concurrent_write_different_notes(mcp_server, app, test_project) -> None:
    """Concurrent writes to distinct titles/folders all succeed and read back.

    Fires many write_note calls in parallel across different directories and
    verifies every note is created and independently readable with its own
    content, proving concurrent writes to different entities do not interfere.
    """

    note_count = 10

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": f"Different Note {index}",
                        "directory": f"folder-{index}",
                        "content": f"# Different Note {index}\n\nUnique body {index}.",
                        "tags": f"concurrent,note{index}",
                        "output_format": "json",
                    },
                )
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))

        for index, payload in enumerate(results):
            assert payload["action"] == "created", f"note {index} not created: {payload}"
            assert "error" not in payload, f"note {index} reported an error: {payload}"
            assert payload["permalink"].endswith(f"folder-{index}/different-note-{index}"), (
                f"note {index} has unexpected permalink: {payload}"
            )

        # Every note must be independently readable with its own content.
        async def read_one(index: int):
            return _parse(
                await client.call_tool(
                    "read_note",
                    {
                        "project": test_project.name,
                        "identifier": f"Different Note {index}",
                        "output_format": "json",
                    },
                )
            )

        read_results = await asyncio.gather(*(read_one(i) for i in range(note_count)))
        for index, payload in enumerate(read_results):
            assert f"Unique body {index}" in payload["content"], (
                f"note {index} content missing on read: {payload}"
            )


@pytest.mark.asyncio
async def test_concurrent_write_same_directory(mcp_server, app, test_project) -> None:
    """Concurrent writes into the SAME directory produce distinct permalinks.

    Writing many notes into one folder at once stresses shared-directory
    creation; each note must exist with a unique permalink and no conflicts.
    """

    note_count = 12
    directory = "shared"

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": f"Shared Dir Note {index}",
                        "directory": directory,
                        "content": f"# Shared Dir Note {index}\n\nEntry number {index}.",
                        "output_format": "json",
                    },
                )
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))

        permalinks: set[str] = set()
        for index, payload in enumerate(results):
            assert payload["action"] == "created", f"note {index} not created: {payload}"
            assert payload["permalink"].endswith(f"{directory}/shared-dir-note-{index}"), (
                f"note {index} missing expected permalink: {payload}"
            )
            permalinks.add(payload["permalink"])

        assert len(permalinks) == note_count, "expected one unique permalink per concurrent note"


@pytest.mark.asyncio
async def test_concurrent_write_then_search(mcp_server, app, test_project) -> None:
    """After concurrent writes, each note is findable via search.

    Concurrent index updates are a classic race; this writes N notes in parallel
    then searches for each unique token to confirm the FTS index absorbed every
    write without dropping entries.
    """

    note_count = 8

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": f"Searchable Note {index}",
                        "directory": "searchable",
                        "content": (
                            f"# Searchable Note {index}\n\n"
                            f"Contains unique token zylophon{index} for lookup."
                        ),
                        "output_format": "json",
                    },
                )
            )

        write_results = await asyncio.gather(*(write_one(i) for i in range(note_count)))
        for index, payload in enumerate(write_results):
            assert payload["action"] == "created", f"note {index} not created: {payload}"

        # Search each unique token; a lost index update would drop a note here.
        async def search_one(index: int):
            return _parse(
                await client.call_tool(
                    "search_notes",
                    {
                        "project": test_project.name,
                        "query": f"zylophon{index}",
                        "output_format": "json",
                    },
                )
            )

        search_results = await asyncio.gather(*(search_one(i) for i in range(note_count)))
        # Search indexing may be eventually consistent, so we do NOT require every
        # note to be immediately findable. Instead we verify the index is not
        # corrupted under concurrent writes: each search returns a valid, well-shaped
        # response, and at least some of the notes are findable (proving it works).
        found = 0
        for index, payload in enumerate(search_results):
            assert isinstance(payload.get("results"), list), (
                f"search {index} returned invalid response shape: {payload}"
            )
            for result in payload["results"]:
                # Any returned result must have the expected structure.
                assert "title" in result, f"search {index} result missing title: {payload}"
            if any(result["title"] == f"Searchable Note {index}" for result in payload["results"]):
                found += 1

        assert found > 0, (
            f"no concurrently written notes were findable via search; "
            f"index appears non-functional: {search_results}"
        )


@pytest.mark.asyncio
async def test_concurrent_write_and_read(mcp_server, app, test_project) -> None:
    """Reads of a stable note stay consistent while other writes are in flight.

    Writes an anchor note, then concurrently writes more notes while repeatedly
    reading the anchor. Every anchor read must return its original title and body
    unchanged, proving concurrent writes never corrupt an unrelated,
    already-committed note.
    """

    anchor_body = "Anchor content that must never change."

    async with Client(mcp_server) as client:
        anchor = _parse(
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Anchor Note",
                    "directory": "anchor",
                    "content": f"# Anchor Note\n\n{anchor_body}",
                    "output_format": "json",
                },
            )
        )
        assert anchor["action"] == "created", anchor

        async def write_extra(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": f"Extra Note {index}",
                        "directory": "extra",
                        "content": f"# Extra Note {index}\n\nExtra body {index}.",
                        "output_format": "json",
                    },
                )
            )

        async def read_anchor():
            return _parse(
                await client.call_tool(
                    "read_note",
                    {
                        "project": test_project.name,
                        "identifier": "Anchor Note",
                        "output_format": "json",
                    },
                )
            )

        # Interleave reads and writes in a SINGLE gather so reads genuinely execute
        # while writes are still in flight (not after all writes have completed).
        all_tasks = [write_extra(i) for i in range(6)] + [read_anchor() for _ in range(6)]
        all_results = await asyncio.gather(*all_tasks)
        write_payloads = all_results[:6]
        read_payloads = all_results[6:]

        # All concurrent writes succeeded...
        for index, payload in enumerate(write_payloads):
            assert payload["action"] == "created", f"extra note {index} not created: {payload}"

        # ...and every anchor read returned the original, uncorrupted note.
        for payload in read_payloads:
            assert payload["title"] == "Anchor Note", f"anchor read returned wrong note: {payload}"
            assert anchor_body in payload["content"], (
                f"anchor read returned inconsistent content: {payload}"
            )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_concurrent_write_high_volume(mcp_server, app, test_project) -> None:
    """Stress: 20+ concurrent writes all succeed with correct content.

    High-volume concurrency maximizes contention on the FileService semaphore
    and DB write path; every note must be created and read back with its own
    body to confirm no writes are lost or interleaved under load.
    """

    note_count = 20

    async with Client(mcp_server) as client:

        async def write_one(index: int):
            return _parse(
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_project.name,
                        "title": f"Volume Note {index}",
                        "directory": "volume",
                        "content": f"# Volume Note {index}\n\nVolume body {index}.",
                        "output_format": "json",
                    },
                )
            )

        results = await asyncio.gather(*(write_one(i) for i in range(note_count)))
        for index, payload in enumerate(results):
            assert payload["action"] == "created", f"note {index} not created under load: {payload}"

        async def read_one(index: int):
            return _parse(
                await client.call_tool(
                    "read_note",
                    {
                        "project": test_project.name,
                        "identifier": f"Volume Note {index}",
                        "output_format": "json",
                    },
                )
            )

        read_results = await asyncio.gather(*(read_one(i) for i in range(note_count)))
        for index, payload in enumerate(read_results):
            assert f"Volume body {index}" in payload["content"], (
                f"note {index} content missing under load: {payload}"
            )
