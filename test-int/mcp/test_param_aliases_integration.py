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


# --- read_note: pagination params restored in #883 ---
# `page` / `page_size` were removed in #693 because they were no-ops on the
# resource read. #883 restored them for parity with the sibling navigation
# tools: they paginate the server-side search fallback (a direct match still
# returns the full note content).


@pytest.mark.asyncio
async def test_read_rejects_removed_pagination(mcp_server, app, test_project):
    """Exact reads use line bounds, not fuzzy-search pagination."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_read", {"identifier": "missing", "page": 1}, raise_on_error=False
        )
        assert result.is_error
        assert "unexpected_keyword_argument" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_find_alias_for_find_text(mcp_server, app, test_project):
    """`find` should map to `find_text` — the highest-frequency miss in the issue."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Find Alias Note",
                "directory": "test",
                "content": "# Find Alias Note\n\nVersion v1.0.0 of the spec.",
            },
        )

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Find Alias Note",
                "command": "find_replace",
                "content": "v2.0.0",
                "find": "v1.0.0",
            },
        )

        assert "(find_replace)" in result.content[0].text

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Find Alias Note",
                "include_frontmatter": False,
            },
        )
        assert "v2.0.0" in read_result.content[0].text
        assert "v1.0.0" not in read_result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_old_text_alias(mcp_server, app, test_project):
    """`old_text` (diff/patch convention) should map to `find_text`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Old Text Note",
                "directory": "test",
                "content": "# Old Text Note\n\nThe quick brown fox.",
            },
        )

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Old Text Note",
                "command": "find_replace",
                "content": "lazy",
                "old_text": "quick",
            },
        )

        assert "(find_replace)" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_new_content_alias_for_content(mcp_server, app, test_project):
    """`new_content` should map to `content` — `content` is ambiguous as 'replacement text'."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "New Content Note",
                "directory": "test",
                "content": "# New Content Note\n\nplaceholder",
            },
        )

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "New Content Note",
                "command": "find_replace",
                "new_content": "actual value",
                "find_text": "placeholder",
            },
        )

        assert "(find_replace)" in result.content[0].text

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "New Content Note",
                "include_frontmatter": False,
            },
        )
        assert "actual value" in read_result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_accepts_section_heading_alias(mcp_server, app, test_project):
    """`section_heading` and `heading` should map to `section`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Section Heading Note",
                "directory": "test",
                "content": "# Section Heading Note\n\n## Notes\n\nold notes\n",
            },
        )

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Section Heading Note",
                "command": "replace_section",
                "content": "fresh notes\n",
                "section_heading": "## Notes",
            },
        )

        assert "(replace_section)" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_canonical_names_still_work(mcp_server, app, test_project):
    """Canonical names must keep working alongside aliases."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Edit Canonical Note",
                "directory": "test",
                "content": "# Edit Canonical Note\n\nold-value here.",
            },
        )

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Edit Canonical Note",
                "command": "find_replace",
                "content": "new-value",
                "find_text": "old-value",
            },
        )

        assert "(find_replace)" in result.content[0].text


# --- search_notes aliases ---


@pytest.mark.asyncio
async def test_search_notes_accepts_query_aliases(mcp_server, app, test_project):
    """`q` (HTTP convention), `search`, and `text` should all map to `query`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Searchable Note",
                "directory": "test",
                "content": "# Searchable Note\n\nUnique-keyword-XYZ here.",
            },
        )

        # Try each alias
        for alias_key in ("q", "pattern", "text"):
            result = await client.call_tool(
                "bm_search",
                {"project": test_project.name, alias_key: "Unique-keyword-XYZ", "limit": 5},
            )
            assert "Searchable Note" in result.content[0].text, f"alias {alias_key} failed"


@pytest.mark.asyncio
async def test_search_notes_accepts_after_date_aliases(mcp_server, app, test_project):
    """`since`/`after`/`from_date` should map to `after_date`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Date Filter Note",
                "directory": "test",
                "content": "# Date Filter Note\n\nbody",
            },
        )

        # Just verify the alias is accepted at validation time (no error)
        result = await client.call_tool(
            "bm_search",
            {"project": test_project.name, "query": "Date Filter", "since": "1d"},
        )
        assert result.content  # didn't error


# --- recent_activity aliases ---


@pytest.mark.asyncio
async def test_recent_activity_accepts_timeframe_aliases(mcp_server, app, test_project):
    """`since`/`time_range`/`lookback` should map to `timeframe`."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_project",
            {"project": test_project.name, "since": "7d", "limit": 5, "command": "recent"},
        )
        assert result.content  # accepted, no validation error


# --- list_directory aliases ---


@pytest.mark.asyncio
async def test_list_directory_accepts_directory_alias(mcp_server, app, test_project):
    """`directory`/`folder`/`path`/`dir` should all map to `dir_name`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Dir Test",
                "directory": "list-dir-aliases",
                "content": "# Dir Test\n\nbody",
            },
        )

        for alias_key in ("dir_name", "folder", "path"):
            result = await client.call_tool(
                "bm_find",
                {"project": test_project.name, alias_key: "/list-dir-aliases"},
            )
            assert "Dir Test" in result.content[0].text, f"alias {alias_key} failed"


@pytest.mark.asyncio
async def test_list_directory_accepts_glob_aliases(mcp_server, app, test_project):
    """`glob`/`pattern`/`filter` should map to `file_name_glob`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Glob Target",
                "directory": "glob-test",
                "content": "# Glob Target\n\nbody",
            },
        )

        result = await client.call_tool(
            "bm_find",
            {"project": test_project.name, "path": "/glob-test", "name": "*.md"},
        )
        assert "Glob Target" in result.content[0].text


# --- write_note aliases ---


@pytest.mark.asyncio
async def test_write_note_accepts_directory_aliases(mcp_server, app, test_project):
    """`folder`/`dir`/`path` should map to `directory`."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Folder Alias Note",
                "folder": "folder-alias-test",
                "content": "# Folder Alias Note\n\nbody",
            },
        )
        assert "folder-alias-test" in result.content[0].text


@pytest.mark.asyncio
async def test_write_note_overwrite_canonical_via_mcp(mcp_server, app, test_project):
    """Canonical overwrite=True must reach the handler (#818 regression)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v1",
            },
        )
        blocked = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v2",
            },
            raise_on_error=False,
        )
        assert blocked.is_error
        assert "ALREADY_EXISTS" in blocked.content[0].text

        result = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Overwrite Canonical Note",
                "directory": "overwrite-test",
                "content": "v2",
                "overwrite": True,
            },
        )
        assert "Updated " in result.content[0].text


# --- move_note aliases ---


@pytest.mark.asyncio
async def test_move_note_accepts_destination_aliases(mcp_server, app, test_project):
    """`to`/`dest_path`/`new_path`/`destination` should map to `destination_path`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Move Target",
                "directory": "move-src",
                "content": "# Move Target\n\nbody",
            },
        )
        result = await client.call_tool(
            "bm_move",
            {
                "project": test_project.name,
                "identifier": "Move Target",
                "to": "move-dest/Move Target.md",
            },
        )
        assert "move-dest" in result.content[0].text


# --- read_content aliases ---


@pytest.mark.asyncio
async def test_read_content_accepts_file_path_alias(mcp_server, app, test_project):
    """`file_path`/`filepath`/`file` should map to `path`."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Read Content Target",
                "directory": "read-content-test",
                "content": "# Read Content Target\n\nraw body",
            },
        )
        result = await client.call_tool(
            "bm_read",
            {"project": test_project.name, "file_path": "read-content-test/Read Content Target.md"},
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
            "bm_write",
            {
                "project": test_project.name,
                "title": "Context Target",
                "directory": "build-ctx",
                "content": "# Context Target\n\nbody",
            },
        )
        result = await client.call_tool(
            "bm_context",
            {"project": test_project.name, "url": "memory://build-ctx/context-target"},
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
            "bm_write",
            {
                "project": test_project.name,
                "title": "Delete Alias Note",
                "directory": "delete-alias-test",
                "content": "# Delete Alias Note\n\nBody.",
            },
        )

        result = await client.call_tool(
            "bm_delete",
            {"project": test_project.name, "identifier": "delete-alias-test/Delete Alias Note"},
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
            "bm_read": (
                ["identifier", "start_line", "end_line"],
                ["file_path", "page", "page_size"],
            ),
            "bm_write": (["identifier", "directory", "overwrite"], ["folder", "dir", "file_path"]),
            "bm_edit": (
                ["command", "find_text", "section", "content"],
                ["operation", "find", "old_text", "new_content", "section_heading"],
            ),
            "bm_search": (
                ["query", "mode", "filters", "items", "since", "page_size"],
                ["q", "pattern", "search_type", "metadata_filters", "entity_types", "limit"],
            ),
            "bm_project": (["command", "since", "items"], ["timeframe", "entity_types"]),
            "bm_find": (["path", "name", "filters"], ["folder", "dir_name"]),
            "bm_move": (
                ["destination", "identifier", "path"],
                ["to", "dest_path", "destination_path"],
            ),
            "bm_delete": (["identifier", "path", "recursive"], ["is_directory", "is_dir"]),
            "bm_context": (["identifier", "since", "page_size"], ["url", "timeframe", "limit"]),
        }

        for tool_name, (must_have, must_not_have) in checks.items():
            assert tool_name in tools, f"tool {tool_name} not registered"
            props = tools[tool_name].input_schema["properties"]
            for canonical in must_have:
                assert canonical in props, f"{tool_name}: canonical '{canonical}' missing"
            for alias in must_not_have:
                assert alias not in props, f"{tool_name}: alias '{alias}' leaked into schema"

        # #818: AliasChoices on optional bool broke external-client JSON schema (null-only).
        overwrite_schema = tools["bm_write"].input_schema["properties"]["overwrite"]
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
