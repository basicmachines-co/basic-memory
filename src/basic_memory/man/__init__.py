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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol
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

# Matches the whole body under ## SYNOPSIS and ## OPTIONS on a section-1 page, up
# to the blank line before the next `## ` heading (or EOF). The single-block
# _MCP_SYNOPSIS_RE cannot serve SYNOPSIS here: find(1) documents two shell forms
# in two fenced blocks, and the CLI generator collapses them to one, so the whole
# section body — every fenced block — has to be replaced, not just the first.
_SYNOPSIS_BODY_RE = re.compile(r"(## SYNOPSIS\n\n)(.*?)(?=\n+## |\n*\Z)", re.S)
_OPTIONS_RE = re.compile(r"(## OPTIONS\n\n)(.*?)(?=\n+## |\n*\Z)", re.S)


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


# --- Typer-generated SYNOPSIS and OPTIONS (section 1) ---
# The SYNOPSIS shell form and the OPTIONS list on a section-1 page are mechanical:
# they must show exactly the command the Typer command tree advertises, the same
# way section 3's SYNOPSIS/PARAMETERS track the MCP registry. These helpers render
# both from a resolved Click command; scripts/update_man_pages.py runs them over the
# section-1 corpus and a test holds every shipped block byte-equal to the rendering.
#
# The renderers take an already-resolved Click command (Typer builds Click objects
# via typer.main.get_command) rather than importing Typer or Click here, so the
# lightweight `basic_memory.man` import stays free of the CLI stack. The Click
# parameter surface they read is pinned by a structural Protocol instead of ``Any``,
# so an attribute the renderers depend on (``hidden``, say) is accessed directly and
# a param shape missing it fails fast rather than being silently treated as public
# (AGENTS.md: no speculative getattr). The Protocol stays structural, so no Click
# import is pulled in.


class ClickParam(Protocol):
    """The Click parameter attributes these section-1 renderers read.

    A resolved command's ``params`` mix arguments and options; every attribute below
    is public Click API. The option-only ones (``is_flag``, ``hidden``, ``help``) are
    read solely after ``param_type_name == "option"`` has filtered arguments out, so
    the renderers never touch them on an argument even though the Protocol names them.
    """

    param_type_name: str
    name: str
    opts: list[str]
    secondary_opts: list[str]
    required: bool
    is_flag: bool
    multiple: bool
    hidden: bool
    default: Any
    help: str | None


class ClickCommand(Protocol):
    """The resolved Click command surface a section-1 page is rendered from: its params."""

    @property
    def params(self) -> Sequence[ClickParam]: ...


# Option pairs the CLI rejects in combination — ``--json``/``--plain`` guarded by
# _validate_output_flags and ``--local``/``--cloud`` by validate_routing_flags in
# cli/commands/posix.py. Click carries no cross-parameter constraint, so the
# generator must name them here: the SYNOPSIS shows a fully-present pair as one
# ``[--json | --plain]`` alternative rather than two freely-combinable tokens, the
# way the curated pages did. Tuples fix the render order (json before plain). Pairs
# whose members are not both present on a command are left ungrouped.
MUTUALLY_EXCLUSIVE_OPTIONS: tuple[tuple[str, ...], ...] = (
    ("--json", "--plain"),
    ("--local", "--cloud"),
)


def _order_opts(opts: list[str]) -> list[str]:
    """Order an option's spellings short flags first, then long ones.

    ``-p, --project`` reads the way people write it; a stable sort keeps the
    declared order within each group so multi-short or multi-long spellings hold
    their author-chosen sequence.
    """
    return sorted(opts, key=lambda opt: opt.startswith("--"))


def _synopsis_opt(opts: list[str]) -> str:
    """The spelling to show for an option in the shell SYNOPSIS: its long form.

    The long form names the option unambiguously; a short-only option falls back
    to its first (short) spelling.
    """
    longs = [opt for opt in opts if opt.startswith("--")]
    return longs[0] if longs else opts[0]


def _synopsis_option_token(param: ClickParam) -> str:
    """The bracketed SYNOPSIS token for one public option.

    ``[--flag]`` for a boolean flag, ``[--on | --no-on]`` for a boolean pair, and
    ``[--opt METAVAR]`` for a value option (metavar is the parameter name upper-
    cased). A repeatable value option (Click ``multiple``) keeps the ``...``
    repetition notation — ``[--meta META ...]`` — so the page still shows it can be
    passed more than once.
    """
    opt = _synopsis_opt(param.opts)
    if param.secondary_opts:
        return f"[{opt} | {_synopsis_opt(param.secondary_opts)}]"
    if param.is_flag:
        return f"[{opt}]"
    metavar = param.name.upper()
    inner = f"{opt} {metavar} ..." if param.multiple else f"{opt} {metavar}"
    return f"[{inner}]"


def render_cli_synopsis(command_path: str, command: ClickCommand) -> str:
    """Render a section-1 page's shell SYNOPSIS from a resolved Click command.

    Positional arguments come first in declaration order (bare when required,
    bracketed when optional), then every public option as a bracketed token (see
    _synopsis_option_token). Options the CLI rejects in combination
    (MUTUALLY_EXCLUSIVE_OPTIONS) collapse to a single ``[--json | --plain]``
    alternative at the first member's position rather than reading as freely
    combinable. Lines wrap at the code block's width with continuations aligned
    under the command name — mirroring render_synopsis's wrap for the MCP form.
    """
    tokens: list[str] = []
    for param in command.params:
        if param.param_type_name != "argument":
            continue
        metavar = param.name.upper()
        tokens.append(metavar if param.required else f"[{metavar}]")

    options = [
        param for param in command.params if param.param_type_name == "option" and not param.hidden
    ]
    # A mutex pair renders as one grouped token only when both members are actually
    # public options on this command; map each present member's long form to its pair.
    present_longs = {_synopsis_opt(param.opts): param for param in options}
    grouped: dict[str, tuple[str, ...]] = {
        long: pair
        for pair in MUTUALLY_EXCLUSIVE_OPTIONS
        if set(pair) <= present_longs.keys()
        for long in pair
    }
    emitted_pairs: set[tuple[str, ...]] = set()
    for param in options:
        pair = grouped.get(_synopsis_opt(param.opts))
        if pair is not None:
            # Emit the whole group once, at its first member, in the pair's fixed
            # order; skip the remaining members so it is not repeated.
            if pair in emitted_pairs:
                continue
            emitted_pairs.add(pair)
            tokens.append("[" + " | ".join(pair) + "]")
        else:
            tokens.append(_synopsis_option_token(param))

    prefix = f"bm {command_path}"
    indent = " " * (len(prefix) + 1)
    lines: list[str] = []
    line = prefix
    count = 0  # tokens already placed on the current line
    for token in tokens:
        candidate = f"{line} {token}"
        # Wrap only once a line carries a token, so a token longer than the width
        # still lands (overlong but unbroken) rather than looping.
        if count > 0 and len(candidate) > SYNOPSIS_WIDTH:
            lines.append(line)
            line = indent + token
            count = 1
        else:
            line = candidate
            count += 1
    lines.append(line)
    return "\n".join(lines)


def _option_default_note(param: ClickParam) -> str | None:
    """The ``default: ...`` note for an option bullet, or None when there is none.

    A boolean pair reports which flag is on by default (``default: --frontmatter``);
    a bare flag reports nothing, since off is simply its absence; a value option
    reports its default literal when the schema carries one.
    """
    default = param.default
    if param.secondary_opts:
        flags = param.opts if default else param.secondary_opts
        longs = [opt for opt in flags if opt.startswith("--")]
        return f"default: {longs[0] if longs else flags[0]}"
    if param.is_flag or default is None:
        return None
    return f"default: {_default_literal(default)}"


def render_options(command: ClickCommand) -> str:
    """Render a section-1 page's ## OPTIONS body from a resolved Click command.

    Every public option is one bullet, in declaration order (the order --help
    lists them). Reusing render_parameters's conventions — default literals,
    single-line descriptions, bullet shape — the head carries the CLI-specific
    syntax those have no concept of: flag aliases join short-first as
    ``-F, --literal`` and a boolean pair shows both sides as
    ``--frontmatter / --no-frontmatter``. Positional arguments are not options;
    they appear in the SYNOPSIS instead. Returns "" when the command has no
    options.
    """
    bullets: list[str] = []
    for param in command.params:
        if param.param_type_name != "option" or param.hidden:
            continue
        opts_display = ", ".join(_order_opts(param.opts))
        if param.secondary_opts:
            opts_display += f" / {', '.join(_order_opts(param.secondary_opts))}"
        head = f"- **{opts_display}**"
        default_note = _option_default_note(param)
        if default_note is not None:
            head += f" ({default_note})"
        description = _normalise_description(param.help)
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


def extract_cli_synopsis(page_text: str) -> str:
    """The shell form a section-1 page currently shows under ## SYNOPSIS.

    Returns the fenced block's inner text (the ``bm ...`` lines). Raises if the
    section is missing or is not a single fenced block — the shape the CLI
    generator writes and the drift test compares against.
    """
    match = _SYNOPSIS_BODY_RE.search(page_text)
    if match is None:
        raise ValueError("page has no SYNOPSIS block")
    body = match.group(2)
    inner = body.removeprefix("```\n").removesuffix("\n```")
    if not (body.startswith("```\n") and body.endswith("\n```")) or "```" in inner:
        raise ValueError("SYNOPSIS is not a single fenced block")
    return inner


def replace_cli_synopsis(page_text: str, synopsis: str) -> str:
    """Return the page with its whole ## SYNOPSIS body replaced by one fenced block.

    The entire body is replaced, not just the first fence, so a page that shipped
    several shell forms (find(1)) collapses to the one the generator renders. Other
    sections are untouched.
    """
    match = _SYNOPSIS_BODY_RE.search(page_text)
    if match is None:
        raise ValueError("page has no SYNOPSIS block")
    fenced = f"```\n{synopsis}\n```"
    return f"{page_text[: match.start()]}{match.group(1)}{fenced}{page_text[match.end() :]}"


def extract_options(page_text: str) -> str:
    """The bullet body a section-1 page currently shows under ## OPTIONS."""
    match = _OPTIONS_RE.search(page_text)
    if match is None:
        raise ValueError("page has no OPTIONS block")
    return match.group(2)


def replace_options(page_text: str, options: str) -> str:
    """Return the page with its ## OPTIONS body replaced in place; other blocks
    untouched.

    Every bundled section-1 page already carries an OPTIONS heading, so this
    rewrites the block rather than inserting one, and fails fast if a page lacks it.
    """
    match = _OPTIONS_RE.search(page_text)
    if match is None:
        raise ValueError("page has no OPTIONS block")
    return f"{page_text[: match.start()]}{match.group(1)}{options}{page_text[match.end() :]}"


def declare_registry_ownership(page_text: str) -> str:
    """Declare a section-3 page registry-owned — a thin wrapper over declare_ownership."""
    return declare_ownership(page_text, owner="registry")


def declare_ownership(page_text: str, owner: str = "cli") -> str:
    """Rewrite the frontmatter's ``generated:`` field to ``owner`` — nothing else.

    ``generated:`` declares who may rewrite a page's mechanical sections: the MCP
    registry generator (``registry``) or the Typer CLI generator (``cli``). A
    curated body may legally contain a literal ``generated: ...`` line (a YAML
    example, say); only the opening frontmatter block is the generator's to rewrite,
    and only the first such line in it, so the count stays 1.
    """
    frontmatter, fence, body = page_text.partition("\n---\n")
    frontmatter = re.sub(
        r"^generated: \w+$", f"generated: {owner}", frontmatter, count=1, flags=re.M
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
