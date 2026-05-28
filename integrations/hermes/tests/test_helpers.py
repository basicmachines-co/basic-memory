"""Unit tests for the pure helpers in __init__.py."""

import json

import pytest


# ---- _truncate ----


def test_truncate_short_passes_through(bm):
    assert bm._truncate("hello", 10) == "hello"


def test_truncate_long_gets_ellipsis(bm):
    out = bm._truncate("a" * 100, 10)
    assert out.endswith("...")
    assert len(out) == 10


def test_truncate_non_string_coerced(bm):
    assert bm._truncate(42, 10) == "42"


def test_truncate_none(bm):
    assert bm._truncate(None, 10) == ""


# ---- _join_message_content ----


def test_join_string_content(bm):
    assert bm._join_message_content("hello") == "hello"


def test_join_list_of_dicts(bm):
    parts = [{"text": "a"}, {"text": "b"}, {"content": "c"}]
    assert bm._join_message_content(parts) == "a\nb\nc"


def test_join_list_of_strings(bm):
    parts = ["a", "b"]
    assert bm._join_message_content(parts) == "a\nb"


def test_join_mixed(bm):
    parts = ["a", {"text": "b"}, {"foo": "bar"}, "c"]
    assert bm._join_message_content(parts) == "a\nb\nc"


def test_join_none(bm):
    assert bm._join_message_content(None) == ""


# ---- _coerce_bool ----


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("YES", True),
        ("1", True),
        ("y", True),
        ("false", False),
        ("False", False),
        ("NO", False),
        ("0", False),
        ("n", False),
    ],
)
def test_coerce_bool(bm, value, expected):
    assert bm._coerce_bool(value) is expected


def test_coerce_bool_non_bool_passes_through(bm):
    assert bm._coerce_bool(42) == 42
    assert bm._coerce_bool("hello") == "hello"


# ---- _extract_mcp_text ----


def test_extract_mcp_text_passes_json_through(bm, fake_result):
    payload = json.dumps({"permalink": "foo/bar", "title": "T"})
    out = bm._extract_mcp_text(fake_result([payload]))
    assert json.loads(out)["permalink"] == "foo/bar"


def test_extract_mcp_text_wraps_markdown(bm, fake_result):
    md = "# Created note\npermalink: foo/bar"
    out = bm._extract_mcp_text(fake_result([md]))
    parsed = json.loads(out)
    assert parsed["text"] == md


def test_extract_mcp_text_joins_multiple_blocks(bm, fake_result):
    out = bm._extract_mcp_text(fake_result(["a", "b"]))
    parsed = json.loads(out)
    assert parsed["text"] == "a\nb"


def test_extract_mcp_text_empty(bm, fake_result):
    out = bm._extract_mcp_text(fake_result([]))
    assert json.loads(out) == {"ok": True}


def test_extract_mcp_text_error(bm, fake_result):
    out = bm._extract_mcp_text(fake_result(["something broke"], is_error=True))
    assert "error" in json.loads(out)


# ---- _extract_permalink ----


def test_extract_permalink_from_bare_json(bm):
    text = json.dumps({"permalink": "proj/folder/note", "title": "T"})
    assert bm._extract_permalink(text, "fb") == "proj/folder/note"


def test_extract_permalink_from_wrapped_json(bm):
    text = json.dumps({"text": json.dumps({"permalink": "proj/folder/note"})})
    assert bm._extract_permalink(text, "fb") == "proj/folder/note"


def test_extract_permalink_from_wrapped_markdown(bm):
    md = (
        "# Created note\n"
        "project: hermes-jodys-imac\n"
        "file_path: x/y.md\n"
        "permalink: hermes-jodys-imac/folder/slug-name\n"
        "checksum: unknown\n"
    )
    text = json.dumps({"text": md})
    assert bm._extract_permalink(text, "fb") == "hermes-jodys-imac/folder/slug-name"


def test_extract_permalink_from_raw_markdown(bm):
    md = "# Created note\npermalink: proj/folder/slug\n"
    # Raw, not wrapped — strategy 4 path
    assert bm._extract_permalink(md, "fb") == "proj/folder/slug"


def test_extract_permalink_no_match(bm):
    assert bm._extract_permalink('{"foo":"bar"}', "fallback-title") == "fallback-title"


def test_extract_permalink_empty(bm):
    assert bm._extract_permalink("", "fb") == "fb"


def test_extract_permalink_invalid(bm):
    assert bm._extract_permalink("not json or markdown", "fb") == "fb"


def test_extract_permalink_strips_trailing_punct(bm):
    md = "# Created note\npermalink: proj/folder/slug,"
    assert bm._extract_permalink(md, "fb") == "proj/folder/slug"


# ---- _translate_args ----


def test_translate_search(bm):
    tool, args = bm._translate_args("bm_search", {"query": "hi", "limit": 7}, "proj")
    assert tool == "search_notes"
    assert args == {"project": "proj", "query": "hi", "page_size": 7}


def test_translate_search_no_limit(bm):
    tool, args = bm._translate_args("bm_search", {"query": "hi"}, "proj")
    assert tool == "search_notes"
    assert args == {"project": "proj", "query": "hi"}


def test_translate_read(bm):
    tool, args = bm._translate_args("bm_read", {"identifier": "x/y"}, "proj")
    assert tool == "read_note"
    assert args == {"project": "proj", "identifier": "x/y"}


def test_translate_read_workspace_qualified_identifier_self_routes(bm):
    tool, args = bm._translate_args(
        "bm_read",
        {"identifier": "personal/main/scratch/note"},
        "hermes-memory",
    )
    assert tool == "read_note"
    assert args == {"identifier": "personal/main/scratch/note"}


def test_translate_read_org_workspace_qualified_identifier_self_routes(bm):
    tool, args = bm._translate_args(
        "bm_read",
        {"identifier": "basic-memory-7020de4e925843c68c9056c60d101d9e/main/scratch/note"},
        "hermes-memory",
    )
    assert tool == "read_note"
    assert args == {"identifier": "basic-memory-7020de4e925843c68c9056c60d101d9e/main/scratch/note"}


def test_translate_write(bm):
    tool, args = bm._translate_args(
        "bm_write",
        {"title": "T", "content": "C", "folder": "F", "tags": ["a", "b"]},
        "proj",
    )
    assert tool == "write_note"
    assert args == {
        "project": "proj",
        "title": "T",
        "content": "C",
        "directory": "F",
        "tags": ["a", "b"],
    }


def test_translate_write_no_tags(bm):
    tool, args = bm._translate_args(
        "bm_write",
        {"title": "T", "content": "C", "folder": "F"},
        "proj",
    )
    assert tool == "write_note"
    assert "tags" not in args
    assert args["directory"] == "F"


def test_translate_edit_minimal(bm):
    tool, args = bm._translate_args(
        "bm_edit",
        {"identifier": "x", "operation": "append", "content": "more"},
        "proj",
    )
    assert tool == "edit_note"
    assert args == {
        "project": "proj",
        "identifier": "x",
        "operation": "append",
        "content": "more",
    }


def test_translate_edit_find_replace(bm):
    tool, args = bm._translate_args(
        "bm_edit",
        {
            "identifier": "x",
            "operation": "find_replace",
            "content": "new",
            "find_text": "old",
        },
        "proj",
    )
    assert args["find_text"] == "old"


def test_translate_edit_replace_section(bm):
    tool, args = bm._translate_args(
        "bm_edit",
        {
            "identifier": "x",
            "operation": "replace_section",
            "content": "new",
            "section": "## Notes",
        },
        "proj",
    )
    assert args["section"] == "## Notes"


def test_translate_context(bm):
    tool, args = bm._translate_args("bm_context", {"url": "memory://x", "depth": 2}, "proj")
    assert tool == "build_context"
    assert args == {"project": "proj", "url": "memory://x", "depth": 2}


def test_translate_context_workspace_qualified_url_self_routes(bm):
    tool, args = bm._translate_args(
        "bm_context",
        {"url": "memory://personal/main/scratch/note", "depth": 1},
        "hermes-memory",
    )
    assert tool == "build_context"
    assert args == {"url": "memory://personal/main/scratch/note", "depth": 1}


def test_translate_context_org_workspace_qualified_url_self_routes(bm):
    tool, args = bm._translate_args(
        "bm_context",
        {
            "url": "memory://basic-memory-7020de4e925843c68c9056c60d101d9e/main/scratch/note",
            "depth": 1,
        },
        "hermes-memory",
    )
    assert tool == "build_context"
    assert args == {
        "url": "memory://basic-memory-7020de4e925843c68c9056c60d101d9e/main/scratch/note",
        "depth": 1,
    }


def test_translate_delete(bm):
    tool, args = bm._translate_args("bm_delete", {"identifier": "x"}, "proj")
    assert tool == "delete_note"
    assert args == {"project": "proj", "identifier": "x"}


def test_translate_move(bm):
    tool, args = bm._translate_args(
        "bm_move", {"identifier": "x", "new_folder": "archive/2026"}, "proj"
    )
    assert tool == "move_note"
    assert args == {
        "project": "proj",
        "identifier": "x",
        "destination_folder": "archive/2026",
    }


def test_translate_recent_defaults(bm):
    tool, args = bm._translate_args("bm_recent", {}, "proj")
    assert tool == "recent_activity"
    assert args == {"project": "proj"}


def test_translate_recent_full(bm):
    tool, args = bm._translate_args(
        "bm_recent",
        {"timeframe": "2 weeks", "limit": 25, "type": "entity"},
        "proj",
    )
    assert tool == "recent_activity"
    assert args == {
        "project": "proj",
        "timeframe": "2 weeks",
        "page_size": 25,
        "type": "entity",
    }


# ---- Per-call project routing ----


def test_translate_uses_default_project_when_no_override(bm):
    """Existing behavior preserved: with no project override, the configured
    default flows through."""
    _, args = bm._translate_args("bm_search", {"query": "hi"}, "default-proj")
    assert args["project"] == "default-proj"
    assert "project_id" not in args


def test_translate_uses_project_name_override(bm):
    """Agent passes project="main" → that name reaches BM, not the default."""
    _, args = bm._translate_args("bm_search", {"query": "hi", "project": "main"}, "default-proj")
    assert args["project"] == "main"
    assert "project_id" not in args


def test_translate_uses_project_id_override(bm):
    """Agent passes project_id=<uuid> → reaches BM as project_id, with no
    project name in the call (would be redundant and risk server-side
    precedence surprises)."""
    uuid = "bf2a4c1e-d77f-4b7a-9c3e-5d8a1f0e2b6d"
    _, args = bm._translate_args("bm_search", {"query": "hi", "project_id": uuid}, "default-proj")
    assert args["project_id"] == uuid
    assert "project" not in args


def test_translate_project_id_wins_when_both_supplied(bm):
    """If the agent passes both, project_id is the more specific identifier
    (UUID across workspaces) and takes precedence. Only project_id reaches BM."""
    uuid = "bf2a4c1e-d77f-4b7a-9c3e-5d8a1f0e2b6d"
    _, args = bm._translate_args(
        "bm_search",
        {"query": "hi", "project": "main", "project_id": uuid},
        "default-proj",
    )
    assert args["project_id"] == uuid
    assert "project" not in args


def test_translate_routing_coerces_to_string(bm):
    """Defensive: if a model passes a non-string identifier (e.g. an int),
    coerce rather than crash. BM accepts strings."""
    _, args = bm._translate_args("bm_search", {"query": "hi", "project_id": 12345}, "default-proj")
    assert args["project_id"] == "12345"


@pytest.mark.parametrize(
    "tool,base_args",
    [
        ("bm_search", {"query": "x"}),
        ("bm_read", {"identifier": "x"}),
        ("bm_write", {"title": "t", "content": "c", "folder": "f"}),
        ("bm_edit", {"identifier": "x", "operation": "append", "content": "c"}),
        ("bm_context", {"url": "memory://x"}),
        ("bm_delete", {"identifier": "x"}),
        ("bm_move", {"identifier": "x", "new_folder": "f"}),
        ("bm_recent", {}),
    ],
)
def test_translate_routing_works_for_every_tool(bm, tool, base_args):
    """Routing applies uniformly across every per-project tool. Global
    discovery tools (bm_projects, bm_workspaces) are tested separately."""
    args_with = dict(base_args, project="main")
    _, out = bm._translate_args(tool, args_with, "default-proj")
    assert out["project"] == "main"

    args_with_id = dict(base_args, project_id="e1d3a5b8-0492-4c1f-8e7d-2a4b6c8d0e2f")
    _, out = bm._translate_args(tool, args_with_id, "default-proj")
    assert out["project_id"] == "e1d3a5b8-0492-4c1f-8e7d-2a4b6c8d0e2f"
    assert "project" not in out

    _, out = bm._translate_args(tool, base_args, "default-proj")
    assert out["project"] == "default-proj"


# ---- Global discovery tools (bm_projects, bm_workspaces) ----


def test_translate_bm_projects_no_routing(bm):
    """bm_projects is a global discovery tool — it lists across all projects
    and workspaces. _translate_args must NOT inject a default project
    (would make BM scope the listing) and MUST request JSON so the agent
    can parse identifiers out of the response."""
    tool, out = bm._translate_args("bm_projects", {}, "default-proj")
    assert tool == "list_memory_projects"
    assert "project" not in out
    assert "project_id" not in out
    assert out == {"output_format": "json"}


def test_translate_bm_workspaces_no_routing(bm):
    tool, out = bm._translate_args("bm_workspaces", {}, "default-proj")
    assert tool == "list_workspaces"
    assert "project" not in out
    assert "project_id" not in out
    assert out == {"output_format": "json"}


def test_translate_global_tools_ignore_project_kwargs(bm):
    """Even if a confused caller passes project/project_id to a global tool,
    those args are dropped — BM doesn't accept them and silently scoping
    the listing would be worse than ignoring the args."""
    _, out = bm._translate_args(
        "bm_projects",
        {"project": "main", "project_id": "e1d3a5b8-0492-4c1f-8e7d-2a4b6c8d0e2f"},
        "default-proj",
    )
    assert "project" not in out
    assert "project_id" not in out


# ---- TOOL_SCHEMAS routing properties ----


def test_every_tool_schema_advertises_project_routing(bm):
    """Every per-project bm_* tool must expose `project` and `project_id` so
    the agent sees them in the tool surface. Regression: forgetting to add
    routing props to a new tool would silently lock the agent into the
    active project — exactly the friction Drew's note flagged.

    Global discovery tools (bm_projects, bm_workspaces) are excluded — they
    list across projects/workspaces and don't take routing args."""
    for schema in bm.TOOL_SCHEMAS:
        props = schema["parameters"]["properties"]
        if schema["name"] in bm._GLOBAL_TOOLS:
            assert "project" not in props, (
                f"{schema['name']} is a global tool; should not have project prop"
            )
            assert "project_id" not in props, (
                f"{schema['name']} is a global tool; should not have project_id prop"
            )
            continue
        assert "project" in props, f"{schema['name']} missing project prop"
        assert "project_id" in props, f"{schema['name']} missing project_id prop"
        # Routing is always optional — never in `required`.
        required = schema["parameters"].get("required", [])
        assert "project" not in required
        assert "project_id" not in required


# ---- _default_project / _hostname ----


def test_default_project_format(bm):
    p = bm._default_project()
    assert p.startswith("hermes-")
    # Hostnames are lowercased and stripped
    assert " " not in p


def test_hostname_lowercased(bm, monkeypatch):
    monkeypatch.setattr(bm.socket, "gethostname", lambda: "Some.Long.Host")
    assert bm._hostname() == "some"


# ---- TOOL_SCHEMAS ----


def test_tool_schemas_complete(bm):
    names = {s["name"] for s in bm.TOOL_SCHEMAS}
    expected = {
        "bm_search",
        "bm_read",
        "bm_write",
        "bm_edit",
        "bm_context",
        "bm_delete",
        "bm_move",
        "bm_recent",
        "bm_projects",
        "bm_workspaces",
    }
    assert names == expected


def test_tool_schemas_have_descriptions(bm):
    for s in bm.TOOL_SCHEMAS:
        assert s["description"], f"{s['name']} missing description"
        assert "parameters" in s
        assert s["parameters"]["type"] == "object"


def test_hermes_to_bm_complete(bm):
    assert set(bm._HERMES_TO_BM.keys()) == {s["name"] for s in bm.TOOL_SCHEMAS}
