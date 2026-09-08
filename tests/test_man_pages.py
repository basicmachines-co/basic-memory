"""Tests for the bundled manual: page references, resolution, and the shipped corpus."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from basic_memory.man import (
    MAN_DIR,
    PageRef,
    bundled_pages,
    declare_ownership,
    declare_registry_ownership,
    extract_cli_synopsis,
    extract_mcp_synopsis,
    extract_options,
    extract_parameters,
    find_page,
    parse_page_ref,
    remove_parameters,
    render_cli_synopsis,
    render_index,
    render_options,
    render_parameters,
    render_synopsis,
    replace_cli_synopsis,
    replace_mcp_synopsis,
    replace_options,
    replace_parameters,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import __all__ as registered_tools

# scripts/ is not a package (no __init__.py) and CI runs pytest with
# --import-mode=importlib, so `from scripts...` fails collection. Load the file
# directly, matching the convention in tests/test_update_versions.py.
MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_man_pages.py"
SPEC = importlib.util.spec_from_file_location("update_man_pages", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
update_man_pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_man_pages)
regenerate_page = update_man_pages.regenerate_page
regenerate_cli_page = update_man_pages.regenerate_cli_page


def _has_parameters_block(page_text: str) -> bool:
    """True when the page carries a ## PARAMETERS section."""
    try:
        extract_parameters(page_text)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("search-notes(3)", PageRef("search-notes", 3)),
        ("search-notes.3", PageRef("search-notes", 3)),
        ("search-notes-3", PageRef("search-notes", 3)),
        ("3/search-notes", PageRef("search-notes", 3)),
        ("man3/search-notes", PageRef("search-notes", 3)),
        ("man3/search-notes(3).md", PageRef("search-notes", 3)),
        ("search-notes%283%29", PageRef("search-notes", 3)),
        ("search_notes", PageRef("search-notes", None)),
        ("SEARCH_NOTES", PageRef("search-notes", None)),
        ("/write-note/", PageRef("write-note", None)),
        ("bm(1)", PageRef("bm", 1)),
    ],
)
def test_parse_page_ref_accepts_every_common_spelling(text: str, expected: PageRef) -> None:
    assert parse_page_ref(text) == expected


def test_page_ref_display_shows_section_when_present() -> None:
    assert PageRef("search-notes", 3).display == "search-notes(3)"
    assert PageRef("search-notes", None).display == "search-notes"


@pytest.mark.parametrize("text", ["", "/", "docs/search-notes"])
def test_parse_page_ref_rejects_what_cannot_name_a_page(text: str) -> None:
    with pytest.raises(ValueError, match="not a manual page reference"):
        parse_page_ref(text)


@pytest.mark.parametrize("text", ["man3", "(3)", "nope"])
def test_parse_page_ref_leaves_unknown_names_to_resolution(text: str) -> None:
    # Parse, don't validate: an odd name is still a name. It simply resolves to
    # nothing, which is the caller's "No manual entry" case, not a parse error.
    assert find_page(parse_page_ref(text)) is None


def test_find_page_accepts_the_tool_name_as_an_alias() -> None:
    # chatgpt-search(3) documents the `search` tool; memory://man/search(3) must land there.
    by_alias = find_page(PageRef("search", 3))
    without_section = find_page(PageRef("fetch", None))
    exact = find_page(PageRef("search-notes", 3))
    assert by_alias is not None and by_alias.name == "chatgpt-search"
    assert without_section is not None and without_section.name == "chatgpt-fetch"
    assert exact is not None and exact.name == "search-notes"


def test_find_page_uses_named_section_or_lowest() -> None:
    assert find_page(PageRef("search-notes", 3)) is not None
    assert find_page(PageRef("search-notes", None)) is not None
    assert find_page(PageRef("search-notes", 5)) is None
    assert find_page(PageRef("no-such-page", None)) is None


def test_bundled_pages_are_well_formed_and_sorted() -> None:
    pages = bundled_pages()

    assert len(pages) == len(list(MAN_DIR.glob("man[1-9]/*.md")))
    assert [(page.section, page.name) for page in pages] == sorted(
        (page.section, page.name) for page in pages
    )
    for page in pages:
        assert page.path.name == f"{page.title}.md"
        assert page.summary
        assert page.body().startswith(f"# {page.title}")
        # Pages are portable notes: the cloud manual's permalink must not ship.
        assert "permalink:" not in page.read().split("---", 2)[1]
    assert all(page.tool for page in pages if page.section == 3)


# The section-3 corpus and the tool registry are meant to match one to one. Both
# lists change deliberately; this pins the known gaps so a new tool without a page
# (or a page for a retired tool) shows up here instead of going unnoticed.
# The POSIX read-side tools (#1399) ship without section-3 pages for now; the
# manual campaign adds them next. Their wire descriptions stay one-liners.
TOOLS_WITHOUT_PAGES: set[str] = {"cat", "find", "grep", "ls", "man", "tail"}
PAGES_WITHOUT_LOCAL_TOOLS = {"cloud_info"}  # hosted-only; see cloud-info(3)


def test_section_3_matches_the_tool_registry_except_known_gaps() -> None:
    documented = {page.tool for page in bundled_pages() if page.section == 3}

    assert set(registered_tools) - documented == TOOLS_WITHOUT_PAGES
    assert documented - set(registered_tools) == PAGES_WITHOUT_LOCAL_TOOLS


def test_render_synopsis_orders_required_first_and_wraps() -> None:
    parameters = {
        "required": ["query"],
        "properties": {
            "alpha": {"default": None},
            "query": {"type": "string"},
            "flag": {"default": False},
            "mode": {"default": "text"},
            "count": {"default": 10},
        },
    }
    assert (
        render_synopsis("demo", parameters)
        == 'demo(query, alpha=None, flag=False, mode="text", count=10)'
    )

    wide = {"required": [], "properties": {f"parameter_{i}": {"default": None} for i in range(9)}}
    rendered = render_synopsis("demo_tool", wide)
    assert all(len(line) <= 76 for line in rendered.splitlines())
    assert rendered.splitlines()[1].startswith(" " * len("demo_tool("))
    assert rendered.endswith(")")
    assert render_synopsis("bare", {"properties": {}}) == "bare()"
    # A control character in a default must be escaped, not embedded literally.
    tricky = {"properties": {"sep": {"default": "a\nb"}, "q": {"default": 'say "hi"'}}}
    assert render_synopsis("demo", tricky) == 'demo(sep="a\\nb", q="say \\"hi\\"")'
    # A default factory leaves no schema default; the parameter must still read
    # as optional (name=...), never as a bare required name.
    factory = {"required": ["query"], "properties": {"query": {}, "tags": {}}}
    assert render_synopsis("demo", factory) == "demo(query, tags=...)"


def test_replace_mcp_synopsis_touches_only_the_mcp_block() -> None:
    labelled = "# t\n\n## SYNOPSIS\n\nMCP:\n\n```\nold()\n```\n\nCLI:\n\n```\nbm t\n```\n\n## DESCRIPTION\n"
    bare = "# t\n\n## SYNOPSIS\n\n```\nold()\n```\n\n## DESCRIPTION\n"

    replaced = replace_mcp_synopsis(labelled, "new(a, b=1)")
    assert extract_mcp_synopsis(replaced) == "new(a, b=1)"
    assert "```\nbm t\n```" in replaced  # the CLI block is not the generator's to rewrite
    assert extract_mcp_synopsis(replace_mcp_synopsis(bare, "new()")) == "new()"
    with pytest.raises(ValueError, match="no MCP SYNOPSIS block"):
        replace_mcp_synopsis("# t\n\n## DESCRIPTION\n", "new()")
    with pytest.raises(ValueError, match="no MCP SYNOPSIS block"):
        extract_mcp_synopsis("# t\n\n## DESCRIPTION\n")


@pytest.mark.asyncio
async def test_section_3_synopsis_is_exactly_the_registry_rendering() -> None:
    # The MCP SYNOPSIS block is a mechanical section owned by the registry
    # generator: byte-equal to the rendering of the schema clients receive. A tool
    # change without regenerating the pages fails here, pointing at the fix.
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}

    for page in bundled_pages():
        if page.section != 3 or page.tool not in tools:
            continue
        expected = render_synopsis(page.tool, tools[page.tool].parameters)
        assert extract_mcp_synopsis(page.read()) == expected, (
            f"{page.title} SYNOPSIS is stale; run `just man-regen` and commit the result"
        )


def test_render_parameters_formats_required_first_and_shows_types() -> None:
    parameters = {
        "required": ["query"],
        "properties": {
            "alpha": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "a union"},
            "query": {"type": "string", "description": "what to search for"},
            "page": {"type": "integer", "default": 1},
            "tags": {"type": "array"},
        },
    }
    assert render_parameters("demo", parameters) == (
        "- **query** (string, required) — what to search for\n"
        "- **alpha** (string | null, optional) — a union\n"
        "- **page** (integer, optional, default: 1)\n"
        "- **tags** (array, optional)"
    )
    assert render_parameters("bare", {"properties": {}}) == ""
    # A property with no type info renders without a type name.
    typeless = {"required": ["x"], "properties": {"x": {}}}
    assert render_parameters("demo", typeless) == "- **x** (required)"
    # A list `type` field joins its members, and a multi-line description collapses
    # to a single bullet line (no raw docstring indentation reaches the page).
    list_type = {
        "properties": {
            "kind": {"type": ["string", "null"], "description": "one of\n    a\n    b"},
        }
    }
    assert (
        render_parameters("demo", list_type) == "- **kind** (string | null, optional) — one of a b"
    )
    # A $ref enum + null (how Pydantic emits an optional Enum) resolves the $ref to
    # the enum's underlying JSON type, so it reads `string | null`, not a bare `null`.
    ref_enum = {
        "$defs": {"SortOrder": {"enum": ["asc", "desc"], "type": "string"}},
        "properties": {
            "sort": {
                "anyOf": [{"$ref": "#/$defs/SortOrder"}, {"type": "null"}],
                "default": None,
                "description": "ordering",
            }
        },
    }
    assert (
        render_parameters("demo", ref_enum)
        == "- **sort** (string | null, optional, default: None) — ordering"
    )
    # A $ref that resolves to nothing (no $defs) contributes no type name; the null
    # member still renders, so the union degrades to `null` rather than crashing.
    unresolved_ref = {
        "properties": {"sort": {"anyOf": [{"$ref": "#/$defs/Missing"}, {"type": "null"}]}}
    }
    assert render_parameters("demo", unresolved_ref) == "- **sort** (null, optional)"


def test_replace_parameters_touches_only_the_parameters_block() -> None:
    with_block = (
        "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n"
        "## PARAMETERS\n\n- **a** (string, required)\n\n"
        "## DESCRIPTION\n\nprose\n"
    )
    replaced = replace_parameters(with_block, "- **b** (integer, optional)")
    assert extract_parameters(replaced) == "- **b** (integer, optional)"
    assert "```\nt()\n```" in replaced  # SYNOPSIS untouched
    assert "## DESCRIPTION\n\nprose\n" in replaced  # DESCRIPTION untouched

    # A page with no PARAMETERS: the section is inserted before DESCRIPTION.
    without = "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n## DESCRIPTION\n\nprose\n"
    inserted = replace_parameters(without, "- **c** (string, required)")
    assert extract_parameters(inserted) == "- **c** (string, required)"
    assert "## PARAMETERS\n\n- **c** (string, required)\n\n## DESCRIPTION" in inserted

    # No DESCRIPTION: the section lands after the SYNOPSIS block.
    no_desc = "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n## EXAMPLES\n\nx\n"
    after = replace_parameters(no_desc, "- **d** (string, required)")
    assert extract_parameters(after) == "- **d** (string, required)"
    assert "```\n\n## PARAMETERS\n\n- **d** (string, required)\n\n## EXAMPLES" in after

    with pytest.raises(ValueError, match="nowhere to place PARAMETERS"):
        replace_parameters("# t\n\nno anchors here\n", "- **e** (required)")
    with pytest.raises(ValueError, match="no PARAMETERS block"):
        extract_parameters("# t\n\n## DESCRIPTION\n")


def test_remove_parameters_strips_the_whole_section() -> None:
    # A tool that loses its last parameter must lose its section too: the whole
    # block comes out, leaving one blank line between the surrounding sections and
    # every other section byte-identical.
    with_block = (
        "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n"
        "## PARAMETERS\n\n- **a** (string, required)\n\n"
        "## DESCRIPTION\n\nprose\n"
    )
    stripped = remove_parameters(with_block)
    assert stripped == "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n## DESCRIPTION\n\nprose\n"
    assert "## PARAMETERS" not in stripped
    assert "\n\n\n" not in stripped  # exactly one blank line between headings

    # A page with no PARAMETERS block is returned unchanged.
    without = "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n## DESCRIPTION\n\nprose\n"
    assert remove_parameters(without) == without

    # A block at end of file (no following heading) is removed cleanly, leaving a
    # single trailing newline and no dangling blank line.
    at_end = "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n\n## PARAMETERS\n\n- **a** (string, required)\n"
    assert remove_parameters(at_end) == "# t\n\n## SYNOPSIS\n\n```\nt()\n```\n"


@pytest.mark.parametrize("empty_schema", [{"properties": {}}, {}])
def test_regenerate_page_drops_stale_parameters_when_tool_becomes_parameterless(
    empty_schema: dict[str, object],
) -> None:
    # Transition: a tool that once had parameters now has none. Running the real
    # regeneration path over the old page must strip the generated PARAMETERS block
    # so the page never keeps advertising the removed argument.
    page = (
        "---\ntitle: demo(3)\ngenerated: registry\ntool: demo\n---\n\n"
        "# demo(3)\n\n## SYNOPSIS\n\n```\ndemo(old_arg)\n```\n\n"
        "## PARAMETERS\n\n- **old_arg** (string, required) — soon to be removed\n\n"
        "## DESCRIPTION\n\nprose\n"
    )
    regenerated = regenerate_page(page, "demo", empty_schema)
    assert "## PARAMETERS" not in regenerated
    assert "old_arg" not in regenerated  # the stale bullet text is gone entirely
    assert extract_mcp_synopsis(regenerated) == "demo()"  # SYNOPSIS still rewritten
    assert "## DESCRIPTION\n\nprose\n" in regenerated  # curated section untouched


@pytest.mark.asyncio
async def test_section_3_parameters_is_exactly_the_registry_rendering() -> None:
    # PARAMETERS is registry-owned wherever a tool has parameters: byte-equal to the
    # rendering of the schema clients receive. A tool change without regenerating the
    # pages fails here, pointing at the fix. Tools with no parameters get no section.
    tools = {tool.name: tool for tool in await mcp.list_tools(run_middleware=False)}

    for page in bundled_pages():
        if page.section != 3 or page.tool not in tools:
            continue
        schema = tools[page.tool].parameters
        page_text = page.read()
        if schema.get("properties"):
            expected = render_parameters(page.tool, schema)
            assert extract_parameters(page_text) == expected, (
                f"{page.title} PARAMETERS is stale; run `just man-regen` and commit the result"
            )
        else:
            # A parameterless tool (basic_memory_diagnostics) owns no PARAMETERS
            # section; a leftover block would keep advertising removed arguments.
            assert not _has_parameters_block(page_text), (
                f"{page.title} still carries a PARAMETERS block but {page.tool} has no "
                "parameters; run `just man-regen` and commit the result"
            )


def test_declare_registry_ownership_touches_frontmatter_only() -> None:
    # A curated body may contain a literal `generated: hand` line (a YAML example);
    # only the opening frontmatter block is the generator's to rewrite.
    page = (
        "---\ntitle: t(3)\ngenerated: hand\ntool: t\n---\n\n# t(3)\n\n"
        "```yaml\ngenerated: hand\n```\n"
    )

    flipped = declare_registry_ownership(page)

    assert flipped.startswith("---\ntitle: t(3)\ngenerated: registry\ntool: t\n---\n")
    assert "```yaml\ngenerated: hand\n```" in flipped
    assert declare_registry_ownership(flipped) == flipped


def test_registry_pages_declare_registry_ownership() -> None:
    # generated: declares who may rewrite the mechanical sections. Every page whose
    # tool this build registers is generator-managed; hosted-only pages stay hand.
    for page in bundled_pages():
        if page.section != 3:
            continue
        expected = "registry" if page.tool in set(registered_tools) else "hand"
        assert page.generated == expected, f"{page.title} declares generated: {page.generated}"


def test_section_3_links_resolve_to_bundled_pages() -> None:
    # Section 3 ships in full, so a [[name(3)]] link with no page behind it is a
    # dangling SEE ALSO: a retired tool's page was dropped but not its references.
    for page in bundled_pages():
        for name in re.findall(r"\[\[([^\]]+)\(3\)\]\]", page.body()):
            assert find_page(PageRef(name, 3)) is not None, (
                f"{page.title} links to {name}(3), which is not bundled"
            )


@pytest.mark.parametrize("stale", ["pending release", "unreleased", "fixed at HEAD"])
def test_pages_carry_no_release_pending_claims(stale: str) -> None:
    # The pages ship with the code, so a fix described as pending or unreleased is
    # already in every package that carries the page; such a note is always stale.
    for page in bundled_pages():
        assert stale not in page.body().lower(), f"{page.title} still says '{stale}'"


def test_render_index_marks_pages_whose_tool_this_server_lacks() -> None:
    index = render_index(bundled_pages(), registered_tools=frozenset(registered_tools))
    hosted_only = find_page(PageRef("cloud-info", 3))
    local = find_page(PageRef("search-notes", 3))
    assert hosted_only is not None and local is not None

    assert f"({hosted_only.uri}) — {hosted_only.summary} *(tool not registered" in index
    assert f"({local.uri}) — {local.summary}\n" in index


def test_render_index_lists_every_page_with_uri_and_summary() -> None:
    index = render_index(bundled_pages())

    assert index.startswith("# Basic Memory manual")
    assert "## Section 3 — MCP tools" in index
    for page in bundled_pages():
        assert f"- [{page.title}]({page.uri}) — {page.summary}" in index


# --- Section 1: CLI SYNOPSIS and OPTIONS from the Typer command tree ---
# The shell SYNOPSIS and OPTIONS blocks on a section-1 page are mechanical
# restatements of a `bm` verb's parameters, the CLI counterpart of section 3's
# registry-owned SYNOPSIS/PARAMETERS. These tests hold them byte-equal to the live
# Typer rendering and pin the CLI-specific syntax the section-3 renderers lack.


def _cli_command(page):
    """Resolve the Click command a section-1 page documents, with its `bm` path."""
    command_path = update_man_pages.SECTION1_COMMAND_PATHS.get(page.name, page.name)
    return command_path, update_man_pages.resolve_cli_command(command_path)


def test_render_cli_synopsis_renders_the_shell_form() -> None:
    _, grep = _cli_command(find_page(PageRef("grep", 1)))
    synopsis = render_cli_synopsis("grep", grep)

    assert synopsis.startswith("bm grep PATTERN")
    assert "[--literal]" in synopsis  # a boolean flag renders bare
    assert "[--page PAGE]" in synopsis  # a value option carries a metavar
    assert all(len(line) <= 76 for line in synopsis.splitlines())
    # Continuations align under the command name, like render_synopsis's wrap.
    for line in synopsis.splitlines()[1:]:
        assert line.startswith(" " * len("bm grep "))

    # apropos(1) documents `bm man apropos`, a verb on the man subgroup, so its
    # shell form carries the full command path rather than a bare `bm apropos`.
    apropos_path, apropos = _cli_command(find_page(PageRef("apropos", 1)))
    assert apropos_path == "man apropos"
    assert render_cli_synopsis(apropos_path, apropos).startswith("bm man apropos QUERY")


def test_render_options_includes_shared_and_global_flags() -> None:
    # D2: OPTIONS is the COMPLETE public option list, including the shared output
    # and routing flags the hand-written blocks left out — grep(1) grows from four
    # bullets to the full set. That growth is the point, not a regression.
    _, grep = _cli_command(find_page(PageRef("grep", 1)))
    options = render_options(grep)
    for shared in ("--json", "--plain", "--project", "--project-id", "--local", "--cloud"):
        assert f"- **{shared}**" in options, f"{shared} missing from generated OPTIONS"


def test_render_options_preserves_aliases_and_boolean_pairs() -> None:
    # D3: OPTIONS keeps the CLI syntax section-3 PARAMETERS has no concept of —
    # flag aliases (-F, --literal) and paired booleans (--x / --no-x).
    _, grep = _cli_command(find_page(PageRef("grep", 1)))
    assert "- **-F, --literal** — Literal full-text matching" in render_options(grep)

    _, cat = _cli_command(find_page(PageRef("cat", 1)))
    assert "- **--frontmatter / --no-frontmatter** (default: --frontmatter)" in render_options(cat)


def test_render_cli_synopsis_groups_mutually_exclusive_options() -> None:
    # --json/--plain and --local/--cloud are rejected in combination by the CLI
    # (test_cli_man_lookup asserts exit 1 for both pairs), so the SYNOPSIS must show
    # each pair as a single `|` alternative, not two freely-combinable tokens.
    apropos_path, apropos = _cli_command(find_page(PageRef("apropos", 1)))
    synopsis = render_cli_synopsis(apropos_path, apropos)
    assert "[--json | --plain]" in synopsis
    assert "[--local | --cloud]" in synopsis
    # never the flattened, freely-combinable spelling the constraint forbids
    for flattened in ("[--json]", "[--plain]", "[--local]", "[--cloud]"):
        assert flattened not in synopsis
    # --project/--project-id are NOT mutually exclusive (--project-id takes
    # precedence), so they stay separate tokens rather than being grouped.
    _, find = _cli_command(find_page(PageRef("find", 1)))
    find_synopsis = render_cli_synopsis("find", find)
    assert "[--project PROJECT]" in find_synopsis
    assert "[--project-id PROJECT_ID]" in find_synopsis
    assert "--project |" not in find_synopsis


def test_render_cli_synopsis_keeps_repeatable_options_repeatable() -> None:
    # find --meta is multiple=True: the SYNOPSIS keeps the `...` repetition notation
    # so the page still shows the option can be passed more than once.
    _, find = _cli_command(find_page(PageRef("find", 1)))
    synopsis = render_cli_synopsis("find", find)
    assert "[--meta META ...]" in synopsis
    # a non-repeatable value option carries no repetition notation
    assert "[--fields FIELDS]" in synopsis
    assert "[--fields FIELDS ...]" not in synopsis


@dataclass
class _FakeClickParam:
    """A structural stand-in for a Click parameter (the ClickParam Protocol).

    Lets the visibility branch be exercised without a hidden option in the live
    CLI, and pins that ``param.hidden`` is read directly — a param shape missing it
    would raise rather than default to public, per the fail-fast contract.
    """

    param_type_name: str
    name: str
    opts: list[str]
    secondary_opts: list[str] = field(default_factory=list)
    required: bool = False
    is_flag: bool = False
    multiple: bool = False
    hidden: bool = False
    default: object = None
    help: str | None = None


@dataclass
class _FakeClickCommand:
    params: list[_FakeClickParam]


def test_render_excludes_hidden_options_from_synopsis_and_options() -> None:
    kept = _FakeClickParam("option", "keep", ["--keep"], is_flag=True, help="kept")
    secret = _FakeClickParam("option", "secret", ["--secret"], is_flag=True, hidden=True, help="x")
    command = _FakeClickCommand([kept, secret])

    synopsis = render_cli_synopsis("t", command)
    assert "[--keep]" in synopsis
    assert "--secret" not in synopsis

    options = render_options(command)
    assert "- **--keep**" in options
    assert "--secret" not in options


def test_render_options_is_not_grouped_for_mutually_exclusive_pairs() -> None:
    # The mutex grouping is a SYNOPSIS-only concern: OPTIONS still documents every
    # option as its own bullet, so each flag keeps its own description.
    apropos_path, apropos = _cli_command(find_page(PageRef("apropos", 1)))
    options = render_options(apropos)
    for flag in ("--json", "--plain", "--local", "--cloud"):
        assert f"- **{flag}**" in options


def test_replace_options_touches_only_the_options_block() -> None:
    page = (
        "# t\n\n## SYNOPSIS\n\n```\nbm t\n```\n\n"
        "## OPTIONS\n\n- **--old** — old\n\n"
        "## EXAMPLES\n\nx\n"
    )
    replaced = replace_options(page, "- **--new** — new")
    assert extract_options(replaced) == "- **--new** — new"
    assert "```\nbm t\n```" in replaced  # SYNOPSIS untouched
    assert "## EXAMPLES\n\nx\n" in replaced  # EXAMPLES untouched
    with pytest.raises(ValueError, match="no OPTIONS block"):
        replace_options("# t\n\n## DESCRIPTION\n", "- **--x** — x")
    with pytest.raises(ValueError, match="no OPTIONS block"):
        extract_options("# t\n\n## DESCRIPTION\n")


def test_replace_cli_synopsis_replaces_the_whole_synopsis_body() -> None:
    # find(1) ships two shell forms; the CLI generator collapses them to one, so
    # the whole SYNOPSIS body is replaced, not just the first fenced block.
    two_forms = (
        "# t\n\n## SYNOPSIS\n\n```\nbm t --a\n```\n\n```\nbm t --b\n```\n\n"
        "## DESCRIPTION\n\nprose\n"
    )
    replaced = replace_cli_synopsis(two_forms, "bm t --a --b")
    assert extract_cli_synopsis(replaced) == "bm t --a --b"
    assert replaced.count("```") == 2  # exactly one fenced block remains
    assert "## DESCRIPTION\n\nprose\n" in replaced  # DESCRIPTION untouched
    with pytest.raises(ValueError, match="no SYNOPSIS block"):
        replace_cli_synopsis("# t\n\n## DESCRIPTION\n", "bm t")
    with pytest.raises(ValueError, match="no SYNOPSIS block"):
        extract_cli_synopsis("# t\n\n## DESCRIPTION\n")
    # A SYNOPSIS body that is not one fenced block (unfenced prose, or two blocks)
    # is a stale page the generator has not rewritten yet, not a shell form.
    with pytest.raises(ValueError, match="not a single fenced block"):
        extract_cli_synopsis("# t\n\n## SYNOPSIS\n\nbm t\n\n## DESCRIPTION\n")
    with pytest.raises(ValueError, match="not a single fenced block"):
        extract_cli_synopsis(two_forms)


def test_section_1_synopsis_and_options_are_exactly_the_typer_rendering() -> None:
    # SYNOPSIS and OPTIONS are CLI-owned: byte-equal to the rendering of the Typer
    # command tree. A verb option change without regenerating the pages fails here,
    # pointing at the fix.
    for page in bundled_pages():
        if page.section != 1:
            continue
        command_path, command = _cli_command(page)
        page_text = page.read()
        assert extract_cli_synopsis(page_text) == render_cli_synopsis(command_path, command), (
            f"{page.title} SYNOPSIS is stale; run `just man-regen` and commit the result"
        )
        assert extract_options(page_text) == render_options(command), (
            f"{page.title} OPTIONS is stale; run `just man-regen` and commit the result"
        )


def test_section_1_pages_declare_cli_ownership() -> None:
    # generated: cli declares the Typer generator owns the mechanical sections,
    # the section-1 counterpart of section 3's generated: registry.
    for page in bundled_pages():
        if page.section == 1:
            assert page.generated == "cli", f"{page.title} declares generated: {page.generated}"


def test_regenerate_cli_page_is_idempotent_over_the_shipped_pages() -> None:
    # Round-tripping a shipped page through the generator is a no-op: it is what
    # produced the page. This mirrors `just man-regen` making no diff.
    for page in bundled_pages():
        if page.section != 1:
            continue
        command_path, command = _cli_command(page)
        text = page.read()
        assert regenerate_cli_page(text, command_path, command) == text


def test_declare_ownership_sets_the_named_owner_in_frontmatter_only() -> None:
    # A curated body may contain a literal `generated: ...` line (a YAML example);
    # only the opening frontmatter block is the generator's to rewrite.
    page = "---\ntitle: t(1)\ngenerated: hand\n---\n\n# t(1)\n\n```yaml\ngenerated: hand\n```\n"

    flipped = declare_ownership(page, owner="cli")

    assert flipped.startswith("---\ntitle: t(1)\ngenerated: cli\n---\n")
    assert "```yaml\ngenerated: hand\n```" in flipped
    assert declare_ownership(flipped, owner="cli") == flipped
    # The thin registry wrapper is declare_ownership with a fixed owner.
    assert declare_registry_ownership(page) == declare_ownership(page, owner="registry")
