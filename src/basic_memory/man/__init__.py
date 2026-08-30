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
        ordered.append(f"{name}={_default_literal(prop['default'])}" if "default" in prop else name)

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
