"""Structural section scanning for markdown note bodies.

A section is one heading-bounded span of a note body: the heading line through the
line before the next heading of the same or higher level (subsections stay inside
their parent, so spans nest). Sections are coordinates into canonical content —
body-relative line numbers and utf-8 byte offsets — never a copy of it (SPEC-47 /
issue #1403).

The scan is structural, not semantic: it runs on every markdown parse regardless of
the ``bm_parse_semantics`` opt-out, so graph-silent notes (e.g. PDF extraction text)
are still section-addressable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from markdown_it import MarkdownIt

# Path segments join on "/" for storage and selector matching. A heading whose text
# itself contains "/" cannot be addressed by full-path selector; suffix matching and
# duplicate indexes remain available for it.
HEADING_PATH_SEPARATOR = "/"

# Plain parser: heading boundaries need no observation/relation plugins, and the
# token stream already skips fenced code blocks and handles setext headings.
_md = MarkdownIt()

# markdown-it normalizes "\r\n?|\n" to "\n" before assigning token line maps, so
# every coordinate in this module must count lines by that same terminator rule.
# str.split("\n") would fold a lone "\r" into its neighbouring line and shift
# every later heading out of sync with the token stream.
_LINE_TERMINATOR = re.compile(r"\r\n|\r|\n")


def _split_lines(text: str) -> list[str]:
    r"""Split text on markdown-it's line-terminator rule (``\r\n``, lone ``\r``, or ``\n``).

    Mirrors ``str.split("\n")`` shape: a trailing terminator leaves one empty
    artifact element. Returned lines carry no terminator characters, so joining
    with ``"\n"`` and re-splitting round-trips the same list.
    """
    return _LINE_TERMINATOR.split(text)


def _ends_with_terminator(text: str) -> bool:
    """True when the text ends on a line terminator (so split() grew an artifact)."""
    return text.endswith(("\n", "\r"))


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    """One heading-bounded span of a note body, addressed by its heading path.

    Lines are 1-indexed and body-relative (frontmatter excluded); offsets are
    utf-8 byte positions into the encoded body, with ``end_offset`` exclusive so
    ``body.encode()[start_offset:end_offset]`` round-trips the section bytes.
    """

    heading: str
    level: int
    path: tuple[str, ...]
    duplicate_index: int
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int

    @property
    def heading_path(self) -> str:
        """The joined ancestor path stored in the section index."""
        return HEADING_PATH_SEPARATOR.join(self.path)


@dataclass(frozen=True, slots=True)
class _ScannedHeading:
    """One heading token flattened out of the markdown-it stream."""

    level: int
    text: str
    line_index: int


def _scan_headings(body: str) -> list[_ScannedHeading]:
    """Collect heading levels, inline text, and 0-indexed start lines in order."""
    tokens = _md.parse(body)
    headings: list[_ScannedHeading] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        # heading_open tags are always h1-h6; the following inline token carries the
        # authored heading text (trailing ATX hashes already stripped).
        headings.append(
            _ScannedHeading(
                level=int(token.tag[1:]),
                text=tokens[index + 1].content.strip(),
                line_index=token.map[0],
            )
        )
    return headings


def scan_sections(body: str) -> tuple[MarkdownSection, ...]:
    """Scan a frontmatter-stripped note body into its heading-bounded sections.

    The scan is pure and deterministic over the exact string given: read paths
    re-run it on the content bytes being served instead of trusting stored rows,
    so a section slice can never disagree with the text it slices.
    """
    if not body:
        return ()

    headings = _scan_headings(body)
    if not headings:
        return ()

    lines = _split_lines(body)
    # A trailing terminator yields one empty artifact element in the split; it is
    # not a document line, but its start offset is exactly the body's byte length,
    # which makes it the natural exclusive end for the final section.
    line_count = len(lines) - 1 if _ends_with_terminator(body) else len(lines)

    # Offsets index the ORIGINAL bytes, so each line advances by its own
    # terminator's width ("\r\n" is two bytes; terminators are ASCII, so their
    # str length is their utf-8 byte length). findall yields one terminator per
    # split boundary, i.e. one for every line but the last.
    terminators = _LINE_TERMINATOR.findall(body)
    line_start_offsets: list[int] = []
    offset = 0
    for index, line in enumerate(lines):
        line_start_offsets.append(offset)
        offset += len(line.encode("utf-8"))
        if index < len(terminators):
            offset += len(terminators[index])
    total_bytes = offset

    def line_offset(line_index: int) -> int:
        return line_start_offsets[line_index] if line_index < len(lines) else total_bytes

    sections: list[MarkdownSection] = []
    # Stack of (level, heading) ancestors: a new heading pops everything at its own
    # level or deeper, so the stack always spells the open path down to it.
    path_stack: list[tuple[int, str]] = []
    duplicate_counts: dict[tuple[str, ...], int] = {}
    for position, heading in enumerate(headings):
        while path_stack and path_stack[-1][0] >= heading.level:
            path_stack.pop()
        path_stack.append((heading.level, heading.text))
        path = tuple(text for _, text in path_stack)
        duplicate_index = duplicate_counts.get(path, 0)
        duplicate_counts[path] = duplicate_index + 1

        # A section ends at the next heading of the same or higher level; the
        # heading-level cap of 6 bounds this look-ahead to linear total work.
        end_line_index = line_count
        for later in headings[position + 1 :]:
            if later.level <= heading.level:
                end_line_index = later.line_index
                break

        sections.append(
            MarkdownSection(
                heading=heading.text,
                level=heading.level,
                path=path,
                duplicate_index=duplicate_index,
                start_line=heading.line_index + 1,
                end_line=end_line_index,
                start_offset=line_start_offsets[heading.line_index],
                end_offset=line_offset(end_line_index),
            )
        )
    return tuple(sections)


# --- Read-time slicing (section=, lines=, max_tokens=) ---
#
# The note-read API re-runs scan_sections on the exact content it serves instead
# of consulting stored note_section rows: derived rows are eventually consistent,
# while these helpers are pure over the string in hand, so a slice can never
# disagree with the text it slices.

# Heuristic token budget: the repo carries no tokenizer dependency, so max_tokens
# is charged in characters at ~4 chars/token (the common English-plus-markdown
# approximation).
_CHARS_PER_TOKEN = 4

# The explicit truncation marker: appended as a final line, excluded from the
# budget, and naming the exact document-absolute line to resume reading from.
TRUNCATION_MARKER_TEMPLATE = (
    "… [truncated at max_tokens={max_tokens}; continue with lines={continue_line}-]"
)

_DUPLICATE_INDEX_SUFFIX = re.compile(r"\[(\d+)\]\s*$")
# ATX headings require the space after the hashes, so only "# Heading" forms are
# stripped from selector segments; "#tag"-like text stays literal.
_ATX_HEADING_PREFIX = re.compile(r"^#{1,6} ")


@dataclass(frozen=True, slots=True)
class SectionSelector:
    """A parsed ``section=`` selector: path segments plus a duplicate index.

    ``raw`` keeps the caller's original spelling for error messages. Bare
    selectors address duplicate index 0 (the first section with that path).
    """

    raw: str
    segments: tuple[str, ...]
    duplicate_index: int


def parse_section_selector(selector: str) -> SectionSelector | None:
    """Parse a section selector, or return None when it is malformed.

    Accepted forms: ``"Decisions"``, ``"Auth/Decisions"`` (path form),
    ``"Heading[1]"`` (duplicate index on the last segment), and heading-styled
    segments like ``"## Auth"`` (leading hashes plus one space are stripped).
    """
    text = selector.strip()
    duplicate_index = 0
    suffix = _DUPLICATE_INDEX_SUFFIX.search(text)
    if suffix:
        duplicate_index = int(suffix.group(1))
        text = text[: suffix.start()].rstrip()

    segments: list[str] = []
    for segment in text.split(HEADING_PATH_SEPARATOR):
        cleaned = _ATX_HEADING_PREFIX.sub("", segment.strip()).strip()
        if not cleaned:
            return None
        segments.append(cleaned)
    return SectionSelector(raw=selector, segments=tuple(segments), duplicate_index=duplicate_index)


@dataclass(frozen=True, slots=True)
class LineRange:
    """A 1-indexed inclusive document-absolute line range; ``end=None`` reads to EOF."""

    start: int
    end: int | None


def parse_line_range(value: str) -> LineRange | None:
    """Parse ``"N-M"``, ``"N-"`` (to end), or ``"N"``; return None when malformed."""
    first, dash, last = (part.strip() for part in value.strip().partition("-"))
    if not first.isdigit():
        return None
    start = int(first)
    if start < 1:
        return None
    if not dash:
        return LineRange(start=start, end=start)
    if not last:
        return LineRange(start=start, end=None)
    if not last.isdigit():
        return None
    end = int(last)
    if end < start:
        return None
    return LineRange(start=start, end=end)


@dataclass(frozen=True, slots=True)
class NoteSlice:
    """One served slice of a note, with document-absolute coordinates.

    ``content`` never carries a frontmatter block unless an explicit line range
    covered it; line numbers always count over the full stored document
    (frontmatter included), matching line-range follow-up reads.
    """

    content: str
    section: str | None
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    continue_line: int | None


@dataclass(frozen=True, slots=True)
class NoteSliceError:
    """A section lookup failure: unknown, ambiguous, or out-of-range selector."""

    message: str


def _frontmatter_line_count(full_text: str) -> int:
    """Count the leading lines occupied by a well-formed opening frontmatter block.

    Structural, delimiter-only rule (top-of-file ``---`` fence pairs, matching
    ``mcp.note_reads.parse_opening_frontmatter``); YAML validity is deliberately
    not checked here — the markdown layer stays free of YAML parsing. Returns 0
    when no terminated block opens the document.
    """
    lines = _split_lines(full_text)
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def _section_labels(sections: tuple[MarkdownSection, ...]) -> list[str]:
    """Render heading paths in document order, bracket-suffixed for duplicates."""
    path_counts = Counter(section.heading_path for section in sections)
    return [
        f"{section.heading_path}[{section.duplicate_index}]"
        if path_counts[section.heading_path] > 1
        else section.heading_path
        for section in sections
    ]


def _find_section(
    sections: tuple[MarkdownSection, ...],
    selector: SectionSelector,
    note_title: str | None,
) -> tuple[MarkdownSection, str] | NoteSliceError:
    """Match a selector against scanned sections, returning the section and its label.

    A selector matches when a section's full path ends with the selector segments
    (suffix match), so ``"Decisions"`` finds ``("Auth", "Decisions")`` and
    ``"Auth/Decisions"`` disambiguates. Comparison is exact and case-sensitive.
    """
    note_label = f" in note '{note_title}'" if note_title else ""
    matches = [
        section
        for section in sections
        if section.path[-len(selector.segments) :] == selector.segments
    ]
    if not matches:
        available = ", ".join(_section_labels(sections)) or "(note has no headings)"
        return NoteSliceError(
            f"Section '{selector.raw}' not found{note_label}. Available sections: {available}"
        )

    distinct_paths = list(dict.fromkeys(section.heading_path for section in matches))
    if len(distinct_paths) > 1:
        return NoteSliceError(
            f"Section '{selector.raw}' is ambiguous{note_label}. "
            f"Qualify it: {', '.join(distinct_paths)}"
        )

    # All matches share one full path; document order equals duplicate_index order.
    for section in matches:
        if section.duplicate_index == selector.duplicate_index:
            label = (
                f"{section.heading_path}[{section.duplicate_index}]"
                if len(matches) > 1
                else section.heading_path
            )
            return section, label
    return NoteSliceError(
        f"Section '{selector.raw}' not found{note_label}: {len(matches)} section(s) share "
        f"the path '{distinct_paths[0]}' (duplicate indexes 0-{len(matches) - 1})."
    )


def _token_budget_line_count(text: str, text_lines: list[str], budget_chars: int) -> int | None:
    """Return how many leading lines of ``text`` to keep, or None when no cut applies.

    Cut preference: section boundary first, then paragraph (blank-line) boundary,
    then the last full line within budget. Never cuts mid-line, and always keeps
    at least one line so a continue-loop makes forward progress. A single line
    over budget is served whole — there is no boundary to cut at.
    """
    if len(text) <= budget_chars:
        return None
    line_count = len(text_lines)
    if line_count <= 1:
        return None

    # prefix_lengths[k] == len("\n".join(text_lines[:k]))
    prefix_lengths = [0]
    for index, line in enumerate(text_lines):
        separator = 1 if index > 0 else 0
        prefix_lengths.append(prefix_lengths[-1] + separator + len(line))

    def fits(kept: int) -> bool:
        return prefix_lengths[kept] <= budget_chars

    # Section boundaries strictly after the slice start (its own heading, when the
    # slice begins with one, is never a cut point).
    section_cuts = [
        section.start_line - 1 for section in scan_sections(text) if section.start_line > 1
    ]
    fitting = [kept for kept in section_cuts if kept >= 1 and fits(kept)]
    if fitting:
        return max(fitting)

    paragraph_cuts = [
        index for index, line in enumerate(text_lines) if index >= 1 and not line.strip()
    ]
    fitting = [kept for kept in paragraph_cuts if fits(kept)]
    if fitting:
        return max(fitting)

    # Line backstop: the largest whole-line prefix within budget; when even the
    # first line exceeds the budget, keep it anyway — forward progress beats an
    # empty slice.
    for kept in range(line_count - 1, 0, -1):
        if fits(kept):
            return kept
    return 1


def slice_note_content(
    full_text: str,
    *,
    section: SectionSelector | None = None,
    lines: LineRange | None = None,
    max_tokens: int | None = None,
    note_title: str | None = None,
) -> NoteSlice | NoteSliceError:
    """Slice a full stored note document by section, line range, and/or token budget.

    ``full_text`` is the exact content being served (frontmatter included). Section
    and token-budget slices exclude the frontmatter block; an explicit line range
    addresses the full document so continue-reads and cat coordinates agree. All
    returned coordinates are document-absolute 1-indexed lines; served content
    joins lines with "\\n", so CR/CRLF terminators come back normalized.
    """
    if section is not None and lines is not None:
        raise ValueError("section and lines are mutually exclusive")
    if section is None and lines is None and max_tokens is None:
        raise ValueError("slice_note_content requires section, lines, or max_tokens")

    doc_lines = _split_lines(full_text)
    total_lines = 0
    if full_text:
        total_lines = len(doc_lines) - 1 if _ends_with_terminator(full_text) else len(doc_lines)

    section_label: str | None = None
    if section is not None:
        frontmatter_lines = _frontmatter_line_count(full_text)
        # _split_lines strips terminators, so this join is the terminator-
        # normalized document suffix: re-splitting it yields these exact lines,
        # and scanned coordinates translate by the frontmatter offset.
        body = "\n".join(doc_lines[frontmatter_lines:])
        found = _find_section(scan_sections(body), section, note_title)
        if isinstance(found, NoteSliceError):
            return found
        matched, section_label = found
        start_line = frontmatter_lines + matched.start_line
        end_line = frontmatter_lines + matched.end_line
    elif lines is not None:
        start_line = lines.start
        end_line = min(lines.end, total_lines) if lines.end is not None else total_lines
    else:
        # max_tokens alone budgets the whole body: slices never carry frontmatter.
        frontmatter_lines = _frontmatter_line_count(full_text)
        start_line = frontmatter_lines + 1
        end_line = total_lines

    selected = doc_lines[start_line - 1 : end_line]
    content = "\n".join(selected)
    truncated = False
    continue_line: int | None = None
    if max_tokens is not None:
        kept = _token_budget_line_count(content, selected, max_tokens * _CHARS_PER_TOKEN)
        if kept is not None:
            continue_line = start_line + kept
            end_line = start_line + kept - 1
            marker = TRUNCATION_MARKER_TEMPLATE.format(
                max_tokens=max_tokens, continue_line=continue_line
            )
            content = "\n".join(selected[:kept]) + "\n" + marker
            truncated = True

    return NoteSlice(
        content=content,
        section=section_label,
        start_line=start_line,
        end_line=end_line,
        total_lines=total_lines,
        truncated=truncated,
        continue_line=continue_line,
    )
