"""
Integration tests for MCP tool parameter aliases.

Verifies that MCP tools accept training-data-friendly parameter aliases
(via Pydantic AliasChoices) alongside the canonical names, so models
that reach for `offset`/`limit`/`find`/`old_text` etc. don't hit
validation errors on first use.

See: https://github.com/basicmachines-co/basic-memory/issues/690
"""

import pytest
from fastmcp import Client


# --- read_note: pagination params removed in #693 (were no-ops) ---
# The `page` / `page_size` parameters were removed because the API endpoint
# silently dropped them. Search-fallback pagination is unrelated to read_note.


# --- edit_note: find_text / content / section aliases ---


@pytest.mark.asyncio
async def test_edit_note_accepts_find_alias_for_find_text(mcp_server, app, test_project):
    """`find` should map to `find_text` — the highest-frequency miss in the issue."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Find Alias Note",
                "directory": "test",
                "content": "# Find Alias Note\n\nVersion v1.0.0 of the spec.",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "Find Alias Note",
                "operation": "find_replace",
                "content": "v2.0.0",
                "find": "v1.0.0",  # alias for find_text
            },
        )

        assert "Edited note (find_replace)" in result.content[0].text

        read_result = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "Find Alias Note"},
        )
        assert "v2.0.0" in read_result.content[0].text
        assert "v1.0.0" not in read_result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_old_text_alias(mcp_server, app, test_project):
    """`old_text` (diff/patch convention) should map to `find_text`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Old Text Note",
                "directory": "test",
                "content": "# Old Text Note\n\nThe quick brown fox.",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "Old Text Note",
                "operation": "find_replace",
                "content": "lazy",
                "old_text": "quick",
            },
        )

        assert "Edited note (find_replace)" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_new_content_alias_for_content(mcp_server, app, test_project):
    """`new_content` should map to `content` — `content` is ambiguous as 'replacement text'."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "New Content Note",
                "directory": "test",
                "content": "# New Content Note\n\nplaceholder",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "New Content Note",
                "operation": "find_replace",
                "new_content": "actual value",  # alias for content
                "find_text": "placeholder",
            },
        )

        assert "Edited note (find_replace)" in result.content[0].text

        read_result = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "New Content Note"},
        )
        assert "actual value" in read_result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_section_heading_alias(mcp_server, app, test_project):
    """`section_heading` and `heading` should map to `section`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Section Heading Note",
                "directory": "test",
                "content": "# Section Heading Note\n\n## Notes\n\nold notes\n",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "Section Heading Note",
                "operation": "replace_section",
                "content": "fresh notes\n",
                "section_heading": "## Notes",  # alias for section
            },
        )

        assert "Edited note (replace_section)" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_canonical_names_still_work(mcp_server, app, test_project):
    """Canonical names must keep working alongside aliases."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Edit Canonical Note",
                "directory": "test",
                "content": "# Edit Canonical Note\n\nold-value here.",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "Edit Canonical Note",
                "operation": "find_replace",
                "content": "new-value",
                "find_text": "old-value",
            },
        )

        assert "Edited note (find_replace)" in result.content[0].text


# --- search_notes aliases ---


@pytest.mark.asyncio
async def test_search_notes_accepts_query_aliases(mcp_server, app, test_project):
    """`q` (HTTP convention), `search`, and `text` should all map to `query`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Searchable Note",
                "directory": "test",
                "content": "# Searchable Note\n\nUnique-keyword-XYZ here.",
            },
        )

        # Try each alias
        for alias_key in ("q", "search", "text"):
            result = await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    alias_key: "Unique-keyword-XYZ",
                    "limit": 5,  # also testing pagination alias
                },
            )
            assert "Searchable Note" in result.content[0].text, f"alias {alias_key} failed"


@pytest.mark.asyncio
async def test_search_notes_accepts_after_date_aliases(mcp_server, app, test_project):
    """`since`/`after`/`from_date` should map to `after_date`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Date Filter Note",
                "directory": "test",
                "content": "# Date Filter Note\n\nbody",
            },
        )

        # Just verify the alias is accepted at validation time (no error)
        result = await client.call_tool(
            "search_notes",
            {"project": test_project.name, "query": "Date Filter", "since": "1d"},
        )
        assert result.content  # didn't error


# --- recent_activity aliases ---


@pytest.mark.asyncio
async def test_recent_activity_accepts_timeframe_aliases(mcp_server, app, test_project):
    """`since`/`time_range`/`lookback` should map to `timeframe`."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "recent_activity",
            {"project": test_project.name, "since": "7d", "limit": 5},
        )
        assert result.content  # accepted, no validation error


# --- list_directory aliases ---


@pytest.mark.asyncio
async def test_list_directory_accepts_directory_alias(mcp_server, app, test_project):
    """`directory`/`folder`/`path`/`dir` should all map to `dir_name`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Dir Test",
                "directory": "list-dir-aliases",
                "content": "# Dir Test\n\nbody",
            },
        )

        for alias_key in ("directory", "folder", "path", "dir"):
            result = await client.call_tool(
                "list_directory",
                {"project": test_project.name, alias_key: "/list-dir-aliases"},
            )
            assert "Dir Test" in result.content[0].text, f"alias {alias_key} failed"


@pytest.mark.asyncio
async def test_list_directory_accepts_glob_aliases(mcp_server, app, test_project):
    """`glob`/`pattern`/`filter` should map to `file_name_glob`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Glob Target",
                "directory": "glob-test",
                "content": "# Glob Target\n\nbody",
            },
        )

        result = await client.call_tool(
            "list_directory",
            {
                "project": test_project.name,
                "dir_name": "/glob-test",
                "glob": "*.md",
            },
        )
        assert "Glob Target" in result.content[0].text


# --- write_note aliases ---


@pytest.mark.asyncio
async def test_write_note_accepts_directory_aliases(mcp_server, app, test_project):
    """`folder`/`dir`/`path` should map to `directory`."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Folder Alias Note",
                "folder": "folder-alias-test",  # alias
                "content": "# Folder Alias Note\n\nbody",
            },
        )
        assert "folder-alias-test" in result.content[0].text


@pytest.mark.asyncio
async def test_write_note_overwrite_canonical_via_mcp(mcp_server, app, test_project):
    """Canonical overwrite=True must reach the handler (#818 regression)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v1",
            },
        )
        blocked = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v2",
            },
        )
        assert "# Error: Note already exists" in blocked.content[0].text

        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v2",
                "overwrite": True,
            },
        )
        assert "# Updated note" in result.content[0].text


# --- move_note aliases ---


@pytest.mark.asyncio
async def test_move_note_accepts_destination_aliases(mcp_server, app, test_project):
    """`to`/`dest_path`/`new_path`/`destination` should map to `destination_path`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Target",
                "directory": "move-src",
                "content": "# Move Target\n\nbody",
            },
        )
        result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Target",
                "to": "move-dest/Move Target.md",  # alias for destination_path
            },
        )
        assert "move-dest" in result.content[0].text


# --- read_content aliases ---


@pytest.mark.asyncio
async def test_read_content_accepts_file_path_alias(mcp_server, app, test_project):
    """`file_path`/`filepath`/`file` should map to `path`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Read Content Target",
                "directory": "read-content-test",
                "content": "# Read Content Target\n\nraw body",
            },
        )
        result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "file_path": "read-content-test/Read Content Target.md",
            },
        )
        # read_content returns a dict; structured content should include the file
        text = result.content[0].text if result.content else ""
        struct = result.structured_content if hasattr(result, "structured_content") else None
        assert "raw body" in text or (struct and "raw body" in str(struct))


# --- build_context aliases ---


@pytest.mark.asyncio
async def test_build_context_accepts_url_aliases(mcp_server, app, test_project):
    """`uri`/`memory_url` should map to `url`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Context Target",
                "directory": "build-ctx",
                "content": "# Context Target\n\nbody",
            },
        )
        result = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "uri": "memory://build-ctx/context-target",  # alias for url
            },
        )
        # Just verify validation accepted the alias
        assert result.content or result.structured_content


# --- view_note: pagination params removed in #693 (delegates to read_note) ---


# --- delete_note aliases ---


@pytest.mark.asyncio
async def test_delete_note_accepts_is_dir_alias(mcp_server, app, test_project):
    """`is_dir` should map to `is_directory` and route to single-note deletion."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Delete Alias Note",
                "directory": "delete-alias-test",
                "content": "# Delete Alias Note\n\nBody.",
            },
        )

        result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "delete-alias-test/Delete Alias Note",
                "is_dir": False,  # alias for is_directory
            },
        )

        # delete_note returns a bool/dict on success; just assert no error
        assert result.content or result.structured_content


# --- Schema sanity check: aliases must not appear in the advertised schema ---


@pytest.mark.asyncio
async def test_aliases_not_advertised_in_schema(mcp_server, app):
    """The JSON schema sent to models should advertise only canonical names.

    Aliases are accepted at validation time but advertising them would defeat
    the purpose: we want the model to learn the canonical name, with aliases
    as a silent safety net for first-use mistakes.

    The `must_not_have` lists below intentionally include both *accepted*
    aliases (which must stay hidden from the schema) AND *rejected* aliases
    that were considered but deliberately omitted (`offset` for `page`,
    `limit_related` for `max_related`). Listing rejected aliases here acts
    as a future-contributor guard — if anyone re-adds them, this test catches
    it before the bad alias ships.
    """
    async with Client(mcp_server) as client:
        tools = {t.name: t for t in await client.list_tools()}

        # tool_name -> (must_have_canonical, must_not_have_aliases)
        checks = {
            # read_note has no pagination params (#693 — they were no-ops; removed).
            # The must_not_have list still includes the rejected aliases so future
            # contributors don't reintroduce them.
            "read_note": (
                [],
                ["page", "page_size", "offset", "limit", "page_number", "per_page"],
            ),
            "edit_note": (
                ["find_text", "section", "content"],
                ["find", "old_text", "old_content", "search", "new_content", "section_heading"],
            ),
            "search_notes": (
                ["query", "page", "page_size", "note_types", "after_date", "min_similarity"],
                [
                    "q",
                    "search",
                    "offset",
                    "limit",
                    "note_type",
                    "types",
                    "since",
                    "after",
                    "threshold",
                ],
            ),
            "recent_activity": (
                ["type", "timeframe", "page", "page_size"],
                ["types", "kind", "since", "time_range", "lookback", "offset", "limit"],
            ),
            "list_directory": (
                ["dir_name", "file_name_glob"],
                ["directory", "folder", "path", "dir", "glob", "pattern", "filter"],
            ),
            "write_note": (
                ["directory", "overwrite"],
                ["folder", "dir", "path", "force", "replace"],
            ),
            "move_note": (
                ["destination_path", "destination_folder", "is_directory"],
                ["dest_path", "new_path", "to", "destination", "is_dir"],
            ),
            "delete_note": (["is_directory"], ["is_dir"]),
            "read_content": (["path"], ["file_path", "filepath", "file"]),
            # view_note pagination params removed in #693 (delegates to read_note).
            "view_note": (
                [],
                ["page", "page_size", "offset", "limit", "page_number", "per_page"],
            ),
            "build_context": (
                ["url", "timeframe", "page", "page_size", "max_related"],
                ["uri", "memory_url", "since", "offset", "limit", "max_results", "limit_related"],
            ),
            "canvas": (["directory"], ["folder", "dir", "path"]),
        }

        for tool_name, (must_have, must_not_have) in checks.items():
            assert tool_name in tools, f"tool {tool_name} not registered"
            props = tools[tool_name].inputSchema["properties"]
            for canonical in must_have:
                assert canonical in props, f"{tool_name}: canonical '{canonical}' missing"
            for alias in must_not_have:
                assert alias not in props, f"{tool_name}: alias '{alias}' leaked into schema"

        # #818: AliasChoices on optional bool broke external-client JSON schema (null-only).
        overwrite_schema = tools["write_note"].inputSchema["properties"]["overwrite"]
        schema_types: set[str] = set()
        if "type" in overwrite_schema:
            raw = overwrite_schema["type"]
            if isinstance(raw, str):
                schema_types.add(raw)
            else:
                schema_types.update(raw)
        for option in overwrite_schema.get("anyOf", ()):
            if "type" in option:
                schema_types.add(option["type"])
        assert "boolean" in schema_types, (
            f"write_note overwrite must expose boolean in schema, got {overwrite_schema}"
        )
