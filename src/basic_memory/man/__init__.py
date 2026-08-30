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

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
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
                tool=str(tool) if tool is not None else None,
                path=path,
            )
        )
    return tuple(sorted(pages, key=lambda page: (page.section, page.name)))


def find_page(ref: PageRef) -> ManPage | None:
    """Resolve a reference the way man(1) does: the named section, else the lowest."""
    for page in bundled_pages():
        if page.name == ref.name and (ref.section is None or page.section == ref.section):
            return page
    return None


def render_index(pages: tuple[ManPage, ...]) -> str:
    """The apropos view: every page, grouped by section, one line each."""
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
        lines.append(f"- [{page.title}]({page.uri}) — {page.summary}")
    return "\n".join(lines) + "\n"
