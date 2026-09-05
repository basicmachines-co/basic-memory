"""The bundled Basic Memory manual.

Pages are Markdown notes in Unix man-page form, kept in numbered section
directories (``man1/``, ``man3/``, ...). Section 3 — one page per MCP tool — is
canonical here in the package, so every install ships the same pages whether it
is local, cloud, or offline. Three consumers read them: the MCP server serves
them as ``memory://man`` resources, ``bm man <topic>`` renders them in a
terminal, and projects can take copies as ordinary notes.

See ``docs/manual-pages.md`` for the page anatomy and the verification rules.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from basic_memory.file_utils import parse_frontmatter, remove_frontmatter

MAN_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PageRef:
    """A reference to a page, as the caller wrote it: a name and maybe a section."""

    name: str
    section: int | None

    @property
    def display(self) -> str:
        return f"{self.name}({self.section})" if self.section is not None else self.name


_SECTION_DIR_RE = re.compile(r"(?:man)?([1-9])")
_NAME_WITH_SECTION_RE = re.compile(
    r"(?P<name>.+?)(?:\((?P<paren>[1-9])\)|\.(?P<dot>[1-9])|-(?P<dash>[1-9]))"
)


def parse_page_ref(text: str) -> PageRef:
    """Read a page reference in any of the forms people and models actually write.

    Parse, don't validate: the reference is whatever the caller reached for first,
    so every common spelling of the same page is accepted —

        search-notes(3)    search-notes.3    search-notes-3    3/search-notes
        man3/search-notes  search_notes      man3/search-notes(3).md

    — plus percent-encoded variants of the above. The section is optional. Tool
    names with underscores map to the hyphenated page name.

    Raises ValueError for a reference that cannot name a page at all (empty, or
    a path whose directory is not a section).
    """
    ref = unquote(text).strip().strip("/").removesuffix(".md")
    section: int | None = None

    if "/" in ref:
        directory, _, ref = ref.rpartition("/")
        match = _SECTION_DIR_RE.fullmatch(directory)
        if match is None:
            raise ValueError(f"{text!r} is not a manual page reference")
        section = int(match.group(1))

    match = _NAME_WITH_SECTION_RE.fullmatch(ref)
    if match is not None:
        ref = match.group("name")
        suffix = match.group("paren") or match.group("dot") or match.group("dash")
        section = int(suffix)

    name = ref.lower().replace("_", "-")
    if not name:
        raise ValueError(f"{text!r} is not a manual page reference")
    return PageRef(name=name, section=section)


@dataclass(frozen=True)
class ManPage:
    """One bundled page and the frontmatter fields the manual schema guarantees."""

    section: int
    name: str
    summary: str
    generated: str
    tool: str | None
    path: Path

    @property
    def title(self) -> str:
        return f"{self.name}({self.section})"

    @property
    def uri(self) -> str:
        return f"memory://man/{self.title}"

    def read(self) -> str:
        """The page as shipped: frontmatter and body."""
        return self.path.read_text(encoding="utf-8")

    def body(self) -> str:
        """The page without its frontmatter, for rendering."""
        return remove_frontmatter(self.read())


@cache
def bundled_pages() -> tuple[ManPage, ...]:
    """Every page in the package, ordered by section then name."""
    pages: list[ManPage] = []
    for path in sorted(MAN_DIR.glob("man[1-9]/*.md")):
        frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"))
        tool = frontmatter.get("tool")
        pages.append(
            ManPage(
                section=int(frontmatter["section"]),
                name=str(frontmatter["name"]),
                summary=str(frontmatter["summary"]),
                generated=str(frontmatter["generated"]),
                tool=str(tool) if tool is not None else None,
                path=path,
            )
        )
    return tuple(sorted(pages, key=lambda page: (page.section, page.name)))


def find_page(ref: PageRef) -> ManPage | None:
    """Resolve a reference the way man(1) does: the named section, else the lowest.

    A page may be named differently from the tool it documents (chatgpt-search(3)
    documents `search`), so the tool name is an alias for the page name. An exact
    page name wins over an alias.
    """
    candidates = [
        page for page in bundled_pages() if ref.section is None or page.section == ref.section
    ]
    for page in candidates:
        if page.name == ref.name:
            return page
    for page in candidates:
        if page.tool is not None and page.tool.replace("_", "-") == ref.name:
            return page
    return None


# --- Registry-generated SYNOPSIS ---
# The MCP SYNOPSIS block on a section-3 page is a mechanical section: it must show
# exactly the call the tool schema advertises. These helpers render it from the
# schema and splice it into a page; scripts/update_man_pages.py runs them over the
# corpus and a test holds every shipped block byte-equal to the rendering.

SYNOPSIS_WIDTH = 76

# Matches the MCP call block under ## SYNOPSIS. Pages that also show a CLI form
# label the MCP block "MCP:"; MCP-only pages have a single unlabelled block.
_MCP_SYNOPSIS_RE = re.compile(r"(## SYNOPSIS\n\n(?:MCP:\n\n)?```\n)(.*?)(\n```)", re.S)

# Matches the body under ## PARAMETERS. Unlike SYNOPSIS there is no fenced code to
# anchor on, so a lookahead stops the match at the blank line before the next
# `## ` heading (or EOF), leaving that separator out of the captured body.
_PARAMETERS_RE = re.compile(r"(## PARAMETERS\n\n)(.*?)(?=\n+## |\n*\Z)", re.S)


def _default_literal(value: object) -> str:
    """Render a schema default the way the call would be written in Python."""
    if isinstance(value, str):
        # json.dumps escapes quotes, backslashes, and control characters, and its
        # double-quoted output is also a valid Python string literal.
        return json.dumps(value)
    # None, booleans, and numbers all repr() to their Python spelling.
    return repr(value)


def render_synopsis(tool_name: str, parameters: Mapping[str, Any]) -> str:
    """Render a tool's MCP SYNOPSIS call from the JSON schema clients receive.

    Required parameters come first as bare names, then optional ones as
    ``name=default``, each group in schema order — the order clients see. Lines
    wrap at the code block's width with continuations aligned under the first
    argument.
    """
    required: list[str] = parameters.get("required") or []
    properties: Mapping[str, Any] = parameters.get("properties") or {}

    ordered = [name for name in properties if name in required]
    for name, prop in properties.items():
        if name in required:
            continue
        # A default factory leaves no `default` in the schema; render `name=...` so
        # the parameter still reads as optional, not as a bare required name.
        ordered.append(
            f"{name}={_default_literal(prop['default'])}" if "default" in prop else f"{name}=..."
        )

    indent = " " * (len(tool_name) + 1)
    current = f"{tool_name}("
    lines: list[str] = []
    for position, argument in enumerate(ordered):
        piece = argument + ("," if position < len(ordered) - 1 else ")")
        trial = current + piece if current.endswith("(") else f"{current} {piece}"
        if len(trial) > SYNOPSIS_WIDTH and not current.endswith("("):
            lines.append(current)
            current = indent + piece
        else:
            current = trial
    if not ordered:
        current += ")"
    lines.append(current)
    return "\n".join(lines)


def _normalise_description(description: object) -> str:
    """Collapse a schema description to one line for a PARAMETERS bullet.

    Tool descriptions come from the tools' docstring ``Args:`` blocks, so they
    carry the source's line breaks and hanging indentation. A bullet is a single
    line, so runs of whitespace (newlines and indentation included) collapse to
    single spaces; the renderer never reflows or reinterprets the prose beyond that.
    """
    if not description:
        return ""
    return " ".join(str(description).split())


def _schema_type(prop: Mapping[str, Any], defs: Mapping[str, Any] | None = None) -> str:
    """A readable type name for a property's schema, or "" when unknown.

    A plain ``type`` passes through; a list type or a union schema
    (``anyOf``/``oneOf``) joins its member names with `` | `` so a nullable
    string reads ``string | null``. A union member may be a ``$ref`` into the
    schema's ``$defs`` (how Pydantic emits an enum): it resolves to the enum's
    underlying JSON type, so a ``$ref`` enum + null reads ``string | null`` like
    any other nullable union rather than a bare ``null``.
    """
    type_field = prop.get("type")
    if isinstance(type_field, str):
        return type_field
    if isinstance(type_field, list):
        return " | ".join(str(member) for member in type_field)
    for key in ("anyOf", "oneOf"):
        members = prop.get(key)
        if members:
            names: list[str] = []
            for member in members:
                member_type = member.get("type")
                if isinstance(member_type, str):
                    names.append(member_type)
                    continue
                ref = member.get("$ref")
                if isinstance(ref, str) and defs is not None:
                    target = defs.get(ref.rsplit("/", 1)[-1], {})
                    target_type = target.get("type")
                    if isinstance(target_type, str):
                        names.append(target_type)
            if names:
                return " | ".join(names)
    return ""


def render_parameters(tool_name: str, parameters: Mapping[str, Any]) -> str:
    """Render a tool's ## PARAMETERS body from the JSON schema clients receive.

    Required parameters come first, then optional ones, each group in schema order
    (the order clients see) — mirroring render_synopsis. Each bullet names the
    parameter, its type when the schema gives one, whether it is required or
    optional (with the default for optionals that carry one), and its description.
    Returns "" when the schema has no properties, so tools like
    basic_memory_diagnostics get no section.
    """
    required: list[str] = parameters.get("required") or []
    properties: Mapping[str, Any] = parameters.get("properties") or {}
    if not properties:
        return ""

    ordered = [name for name in properties if name in required]
    ordered += [name for name in properties if name not in required]

    bullets: list[str] = []
    for name in ordered:
        prop = properties[name]
        type_name = _schema_type(prop, parameters.get("$defs"))
        if name in required:
            qualifiers = f"{type_name}, required" if type_name else "required"
        else:
            qualifiers = f"{type_name}, optional" if type_name else "optional"
            # A default factory leaves no `default` in the schema; render just
            # `optional`, since there is no literal value to show.
            if "default" in prop:
                qualifiers += f", default: {_default_literal(prop['default'])}"
        head = f"- **{name}** ({qualifiers})"
        description = _normalise_description(prop.get("description"))
        bullets.append(f"{head} — {description}" if description else head)
    return "\n".join(bullets)


def extract_mcp_synopsis(page_text: str) -> str:
    """The MCP call block a page currently shows under ## SYNOPSIS."""
    match = _MCP_SYNOPSIS_RE.search(page_text)
    if match is None:
        raise ValueError("page has no MCP SYNOPSIS block")
    return match.group(2)


def replace_mcp_synopsis(page_text: str, synopsis: str) -> str:
    """Return the page with its MCP SYNOPSIS block replaced; other blocks untouched."""
    match = _MCP_SYNOPSIS_RE.search(page_text)
    if match is None:
        raise ValueError("page has no MCP SYNOPSIS block")
    return f"{page_text[: match.start()]}{match.group(1)}{synopsis}{match.group(3)}{page_text[match.end() :]}"


def extract_parameters(page_text: str) -> str:
    """The bullet body a page currently shows under ## PARAMETERS."""
    match = _PARAMETERS_RE.search(page_text)
    if match is None:
        raise ValueError("page has no PARAMETERS block")
    return match.group(2)


def replace_parameters(page_text: str, parameters: str) -> str:
    """Return the page with its ## PARAMETERS body replaced, inserting the section
    if the page has none.

    An existing block is rewritten in place. Otherwise the section is placed just
    before ## DESCRIPTION if present, else right after the SYNOPSIS block (before
    the next `## ` heading following ## SYNOPSIS). Other blocks are untouched.
    """
    match = _PARAMETERS_RE.search(page_text)
    if match is not None:
        # The lookahead leaves the trailing heading out of the match, so append
        # the rest of the page from match.end() unchanged.
        return f"{page_text[: match.start()]}{match.group(1)}{parameters}{page_text[match.end() :]}"

    block = f"## PARAMETERS\n\n{parameters}\n\n"
    description = page_text.find("## DESCRIPTION")
    if description != -1:
        return f"{page_text[:description]}{block}{page_text[description:]}"

    # No DESCRIPTION anchor: land the section after the SYNOPSIS block, at the
    # next `## ` heading that follows ## SYNOPSIS.
    synopsis = page_text.find("## SYNOPSIS")
    if synopsis != -1:
        following = page_text.find("\n## ", synopsis + len("## SYNOPSIS"))
        if following != -1:
            insert = following + 1  # after the newline, at the `## ` heading
            return f"{page_text[:insert]}{block}{page_text[insert:]}"

    raise ValueError("page has nowhere to place PARAMETERS")


def remove_parameters(page_text: str) -> str:
    """Return the page with any ## PARAMETERS section stripped; unchanged if none.

    A tool that loses its last parameter must lose its section too, so a page can
    never keep advertising removed arguments. The whole section — heading, body,
    and one blank-line separator — comes out, leaving exactly one blank line
    between the surrounding sections (or a clean single trailing newline when the
    section sat at end of file). A page with no PARAMETERS block is returned as is.
    """
    match = _PARAMETERS_RE.search(page_text)
    if match is None:
        return page_text
    # The heading's leading separator lives in the preceding section's trailing
    # newlines, and the lookahead leaves the following separator out of the match;
    # strip both sides to a single blank line so no double gap or dangling section
    # heading is left behind.
    before = page_text[: match.start()].rstrip("\n")
    after = page_text[match.end() :].lstrip("\n")
    return f"{before}\n\n{after}" if after else f"{before}\n"


def declare_registry_ownership(page_text: str) -> str:
    """Flip ``generated: hand`` to ``registry`` — in the frontmatter only.

    A curated body may legally contain a literal ``generated: hand`` line (a YAML
    example, say); only the opening frontmatter block is the generator's to rewrite.
    """
    frontmatter, fence, body = page_text.partition("\n---\n")
    frontmatter = re.sub(
        r"^generated: hand$", "generated: registry", frontmatter, count=1, flags=re.M
    )
    return frontmatter + fence + body


def render_index(pages: tuple[ManPage, ...], registered_tools: frozenset[str] | None = None) -> str:
    """The apropos view: every page, grouped by section, one line each.

    The same corpus serves the local and the hosted server, whose tool sets differ,
    so when the caller knows which tools this server registers, pages for the
    others are marked rather than presented as callable.
    """
    section_titles = {1: "User commands", 3: "MCP tools", 5: "File formats", 7: "Concepts"}
    lines = [
        "# Basic Memory manual",
        "",
        "Read a page with its `memory://man/...` URI, or `bm man <name>` in a shell.",
    ]
    current_section: int | None = None
    for page in pages:
        if page.section != current_section:
            current_section = page.section
            heading = section_titles.get(page.section, f"Section {page.section}")
            lines.extend(["", f"## Section {page.section} — {heading}", ""])
        line = f"- [{page.title}]({page.uri}) — {page.summary}"
        if (
            registered_tools is not None
            and page.tool is not None
            and page.tool not in registered_tools
        ):
            line += " *(tool not registered on this server)*"
        lines.append(line)
    return "\n".join(lines) + "\n"
