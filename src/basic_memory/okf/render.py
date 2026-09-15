"""Pure export values and Markdown rendering; never renders live Wiki bytes."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
import yaml

from basic_memory.file_utils import parse_frontmatter
from basic_memory.markdown.entity_parser import (
    _coerce_to_string,
    normalize_frontmatter_value,
    parse,
)
from basic_memory.markdown.path_links import markdown_link_target
from basic_memory.markdown.plugins import _is_escaped
from basic_memory.repository.entity_repository import file_path_alias
from basic_memory.services.link_resolver import normalize_link_text
from basic_memory.utils import build_permalink_resolution_candidates, generate_permalink

from basic_memory.okf.validation import Document, parse_document


class ExportDumper(yaml.SafeDumper):
    """Retain YAML mapping order while making unordered sets deterministic."""


def represent_set(dumper: ExportDumper, values: set[object]) -> yaml.nodes.MappingNode:
    ordered = sorted(values, key=lambda value: yaml.safe_dump(value, sort_keys=True))
    return dumper.represent_mapping("tag:yaml.org,2002:set", [(value, None) for value in ordered])


ExportDumper.add_representer(set, represent_set)


@dataclass(frozen=True)
class ExportFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class RecordedChange:
    position: int
    path: str
    operation: str
    accepted_at: datetime


@dataclass(frozen=True)
class ExportSnapshot:
    project: str
    files: tuple[ExportFile, ...]
    changes: tuple[RecordedChange, ...] = ()
    permalinks_include_project: bool = True


def markdown_link(label: str, path: str, fragment: str = "") -> str:
    escaped = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    escaped = " ".join(escaped.splitlines())
    href = quote(path, safe="/")
    if fragment:
        href += "#" + quote(fragment, safe="")
    return f"[{escaped}]({href})"


def convert_wikilinks(
    body: str,
    source: str,
    targets: dict[str, str],
    project: str,
    *,
    include_project: bool = True,
    ambiguous_aliases: frozenset[str] = frozenset(),
    permalinks: dict[str, str] | None = None,
    title_targets: dict[str, str] | None = None,
) -> str:
    """Use MarkdownIt's code/escape/link rules while retaining untouched source bytes."""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    project = generate_permalink(project)
    replacements: list[tuple[int, int, str]] = []
    inline_source = ""
    path_aliases: dict[str, list[str]] = {}
    for path in sorted(set(targets.values())):
        path_aliases.setdefault(file_path_alias(path), []).append(path)

    def wikilink(state: StateInline, silent: bool) -> bool:
        start = state.pos
        if not state.src.startswith("[[", start):
            return False
        # Image labels use a nested source with different offsets; keep them literal.
        if silent or state.linkLevel or state.src != inline_source:
            return False
        depth = 1
        end = start + 2
        while end < len(state.src) - 1:
            # Escaped brackets belong to the target, matching the canonical scanner.
            if _is_escaped(state.src, end):
                end += 1
                continue
            pair = state.src[end : end + 2]
            if pair == "[[":
                depth += 1
            elif pair == "]]":
                depth -= 1
                if depth == 0:
                    break
            end += 2 if pair in {"[[", "]]"} else 1
        if depth:
            return False
        raw = state.src[start + 2 : end]
        target, alias = normalize_link_text(raw)
        target, _, fragment = target.partition("#")
        rooted = target.startswith("/")
        resolved = None
        # Explicit relative links bind to their source directory before semantic aliases.
        # Wikilink paths are literal identifiers, so URL decoding must round-trip them.
        relative = markdown_link_target(quote(target, safe="/"), source)
        # A root-relative URI with escaping dot segments could normalize to a real note.
        # Keep that unresolved reference literal rather than inventing a portable edge.
        if relative is None and ".." in PurePosixPath(target).parts:
            return False
        if (
            include_project
            and "/" in target
            and generate_permalink(target.partition("/")[0]) == project
        ):
            relative = None
        if not rooted and "/" in target and relative:
            resolved = targets.get(relative.lstrip("/"))
            if resolved is None:
                resolved = targets.get(relative.lstrip("/") + ".md")
        if not rooted and resolved is None and permalinks:
            # Semantic addresses precede title/path aliases, even when they look like filenames.
            for candidate in build_permalink_resolution_candidates(
                target, project, include_project
            ):
                if target in ambiguous_aliases and candidate != target:
                    break
                if candidate in permalinks:
                    resolved = permalinks[candidate]
                    break
        if not rooted and resolved is None and title_targets:
            resolved = title_targets.get(target)
        if not rooted and resolved is None:
            resolved = targets.get(target)
        if (
            not rooted
            and resolved is None
            and (
                target not in ambiguous_aliases
                or "/" in target
                or target.casefold().endswith(".md")
            )
        ):
            # Forgiving filename spelling is a last resort after exact identities.
            candidates = ([relative] if relative and "/" in target else []) + [target]
            for candidate in candidates:
                path = candidate.lstrip("/")
                if not path.casefold().endswith(".md"):
                    path += ".md"
                matches = path_aliases.get(file_path_alias(path), [])
                if len(matches) == 1:
                    resolved = matches[0]
                    break
        # A missing target stays a broken link, not a guessed edge to another concept.
        # Rooted links already name exact portable file paths; never infer an extension.
        href = target if rooted else "/" + (resolved or target or source)
        replacements.append(
            (start, end + 2, markdown_link(alias or target or fragment, href, fragment))
        )
        state.push("text", "", 0).content = raw
        state.pos = end + 2
        return True

    parser = MarkdownIt()
    parser.inline.ruler.before("link", "okf_wikilink", wikilink)
    # MarkdownIt substitutes U+FFFD for NUL one-for-one, preserving source offsets.
    lines = body.replace("\0", "\ufffd").split("\n")
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)
    document_replacements: list[tuple[int, int, str]] = []
    # Each inline block is parsed separately: backticks in another paragraph must
    # not turn this paragraph into code. Map token text back past list/quote prefixes.
    environment: dict[str, object] = {}
    for token in MarkdownIt().parse(body, environment):
        if token.type != "inline" or not token.map or "[[" not in token.content:
            continue
        replacements.clear()
        inline_source = token.content
        # Retain document references so link labels remain existing Markdown links.
        parser.parseInline(token.content, environment)
        if not replacements:
            continue
        offsets: list[int] = []
        first, last = token.map
        source_line = first
        for text in token.content.split("\n"):
            # MarkdownIt expands continuation indentation tabs. Match the text
            # after indentation while keeping replacement endpoints in raw bytes.
            stripped = text.lstrip(" \t")
            indentation = len(text) - len(stripped)
            column = -1
            while source_line < last:
                column = lines[source_line].find(stripped)
                if column >= 0:
                    break
                source_line += 1
            if source_line == last:
                raise ValueError(f"{source}: cannot locate wikilink source span")
            offset = line_offsets[source_line] + column
            offsets.extend([offset] * indentation)
            offsets.extend(range(offset, offset + len(text) - indentation + 1))
            source_line += 1
        for start, end, replacement in replacements:
            document_replacements.append((offsets[start], offsets[end - 1] + 1, replacement))
    for start, end, replacement in reversed(document_replacements):
        body = body[:start] + replacement + body[end:]

    return body


def render_bundle(snapshot: ExportSnapshot) -> tuple[ExportFile, ...]:
    """Preserve frontmatter and prose; attach only semantics lost by link conversion."""
    targets: dict[str, str] = {}
    permalinks: dict[str, str] = {}
    title_targets: dict[str, str] = {}
    ambiguous: set[str] = set()
    documents: dict[str, Document] = {}
    titles: dict[str, str] = {}
    note_types: dict[str, str] = {}
    for file in snapshot.files:
        if PurePosixPath(file.path).suffix != ".md":
            continue
        document = parse_document(file.content.decode("utf-8"), source=True)
        documents[file.path] = document
        # Resolve using BM's normalized title, but retain authored YAML values in output.
        source_metadata = (
            parse_frontmatter(file.content.decode("utf-8")) if document.has_frontmatter else {}
        )
        title = _coerce_to_string(normalize_frontmatter_value(source_metadata.get("title")))
        title = title if title and title != "None" else PurePosixPath(file.path).stem
        titles[file.path] = title
        note_type = source_metadata.get("type")
        note_types[file.path] = (
            _coerce_to_string(normalize_frontmatter_value(note_type))
            if note_type is not None
            else "note"
        )
        if title in title_targets and title_targets[title] != file.path:
            ambiguous.add(title)
        else:
            title_targets[title] = file.path
        permalink = normalize_frontmatter_value(source_metadata.get("permalink"))
        if isinstance(permalink, str) and permalink:
            # Offline files can violate the indexed uniqueness rule; never choose a winner.
            if permalink in permalinks:
                raise ValueError(
                    f"{file.path}: duplicate permalink {permalink!r} also declared by "
                    f"{permalinks[permalink]}"
                )
            permalinks[permalink] = file.path
    for alias in ambiguous:
        title_targets.pop(alias)
    # Relative path resolution uses exact file identities, separate from semantic names.
    targets.update({file.path: file.path for file in snapshot.files})

    output: list[ExportFile] = []
    directories = {PurePosixPath(".")}
    for file in snapshot.files:
        path = PurePosixPath(file.path)
        directories.update(path.parents)
        if path.suffix != ".md":
            output.append(file)
            continue
        document = documents[file.path]
        metadata = dict(document.metadata)
        metadata["type"] = note_types[file.path]
        metadata.setdefault("tags", [])
        semantic_setting = metadata.get("bm_parse_semantics")
        if not (
            semantic_setting is False
            or (isinstance(semantic_setting, str) and semantic_setting.lower() == "false")
        ):
            semantics = parse(document.body)
            # Observation syntax remains intact and is documented by the profile.
            # Typed relation metadata is needed because a standard link is untyped.
            relations = [relation.model_dump() for relation in semantics.relations]
        else:
            relations = []
        existing = metadata.get("bm", {})
        if not isinstance(existing, dict) or "okf_export" in existing:
            raise ValueError(f"{file.path}: bm.okf_export extension collision")
        metadata["bm"] = {
            **existing,
            "okf_export": {"version": 1, "relations": relations},
        }
        body = convert_wikilinks(
            document.body,
            file.path,
            targets,
            snapshot.project,
            include_project=snapshot.permalinks_include_project,
            ambiguous_aliases=frozenset(ambiguous),
            permalinks=permalinks,
            title_targets=title_targets,
        )
        content = "---\n" + yaml.dump(
            metadata, Dumper=ExportDumper, allow_unicode=True, sort_keys=False
        )
        content += "---\n" + body
        output.append(ExportFile(file.path, content.encode("utf-8")))

    for directory in sorted(directories):
        index_path = str(directory / "index.md")
        lines = ['---\nokf_version: "0.2"\n---\n'] if directory == PurePosixPath(".") else []
        lines.append("# " + (snapshot.project if str(directory) == "." else directory.name) + "\n")
        for file in sorted(snapshot.files, key=lambda file: file.path):
            path = PurePosixPath(file.path)
            if path.parent == directory:
                label = titles.get(file.path, path.stem)
                lines.append("- " + markdown_link(label, path.name))
        for child in sorted(directories):
            if child != directory and child.parent == directory:
                lines.append("- " + markdown_link(child.name, child.name + "/index.md"))
        output.append(ExportFile(index_path, ("\n".join(lines) + "\n").encode("utf-8")))

    log = [
        "# Recorded Basic Memory history\n",
        "Best-effort accepted changes recorded by Basic Memory. "
        "File materialization may lag recorded acceptance. Offline edits are not reconstructed.\n",
    ]
    day = ""
    for change in sorted(
        snapshot.changes, key=lambda change: (change.accepted_at, change.position), reverse=True
    ):
        change_day = change.accepted_at.date().isoformat()
        if change_day != day:
            log.append(f"\n## {change_day}\n")
            day = change_day
        log.append(f"- {change.operation}: {markdown_link(change.path, '/' + change.path)}")
    output.append(ExportFile("log.md", ("\n".join(log) + "\n").encode("utf-8")))
    return tuple(sorted(output, key=lambda file: file.path))
