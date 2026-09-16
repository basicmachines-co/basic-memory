"""OKF v0.2 structural conformance, independent of Basic Memory's index."""

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
import yaml

from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter, strip_bom


class Diagnostic(BaseModel):
    path: str
    rule: str
    message: str


class CheckReport(BaseModel):
    concepts: int = 0
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class Document:
    metadata: dict[str, object]
    body: str
    has_frontmatter: bool


class FrontmatterLoader(yaml.SafeLoader):
    """Keep authored timestamp spelling, including ISO T/Z semantics, intact."""


FrontmatterLoader.yaml_implicit_resolvers = {
    character: [
        (tag, expression) for tag, expression in resolvers if tag != "tag:yaml.org,2002:timestamp"
    ]
    for character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_document(content: str, *, source: bool = False) -> Document:
    """Require a mapping when a YAML fence is present; preserve YAML value types."""
    if source:
        # BM treats unmatched fences and malformed YAML as authored body text.
        # Reuse that classification, then load valid metadata without coercing dates.
        if not has_frontmatter(content):
            return Document({}, content, False)
        try:
            parse_frontmatter(content)
        except ParseError:
            return Document({}, content, False)
    lines = (strip_bom(content) if source else content).splitlines(keepends=True)
    # BM accepts a BOM and leading blank lines; OKF check keeps its on-disk boundary.
    if source:
        while lines and not lines[0].strip():
            lines.pop(0)
    if not lines or (lines[0].rstrip(" \t\r\n") if source else lines[0].strip()) != "---":
        return Document({}, content, False)
    for end in range(1, len(lines)):
        if (lines[end].rstrip(" \t\r\n") if source else lines[end].strip()) == "---":
            break
    else:
        raise ValueError("Unterminated YAML frontmatter")
    try:
        metadata = yaml.load("".join(lines[1:end]), Loader=FrontmatterLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML frontmatter: {error}") from error
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be a YAML mapping")
    return Document(metadata, "".join(lines[end + 1 :]), True)


def check_document(path: str, content: str) -> list[Diagnostic]:
    """Validate only the structural contract in OKF §11, not optional families."""
    diagnostics: list[Diagnostic] = []

    def fail(rule: str, message: str) -> None:
        diagnostics.append(Diagnostic(path=path, rule=rule, message=message))

    try:
        document = parse_document(content)
    except ValueError as error:
        fail("frontmatter", str(error))
        return diagnostics
    name = Path(path).name
    if name not in {"index.md", "log.md"}:
        if not document.has_frontmatter:
            fail("concept.frontmatter", "Concept requires YAML frontmatter (OKF §4)")
        note_type = document.metadata.get("type")
        if not isinstance(note_type, str) or not note_type.strip():
            fail("concept.type", "Concept requires a non-empty string type (OKF §4.1)")
        return diagnostics

    if document.has_frontmatter and (
        name == "log.md" or path != "index.md" or set(document.metadata) != {"okf_version"}
    ):
        fail("reserved.frontmatter", "Only root index.md may carry okf_version (OKF §8–9)")

    tokens = MarkdownIt().parse(document.body)
    if name == "index.md":
        if not any(token.type == "heading_open" for token in tokens):
            fail("index.heading", "Index requires a section heading (OKF §8)")
        # Links are structural entries; prose and code examples are not entries.
        entries: list[bool] = []
        for token in tokens:
            if token.type == "list_item_open":
                entries.append(False)
            elif token.type == "list_item_close":
                if not entries.pop():
                    fail("index.link", "Index entries require standard Markdown links (OKF §8)")
            elif entries and token.type == "inline":
                if any(child.type == "link_open" for child in token.children or []):
                    # A link anywhere in an entry satisfies it, including nested entries.
                    entries = [True] * len(entries)
    else:
        previous: date | None = None
        in_date_group = False
        for index, token in enumerate(tokens):
            if token.type == "list_item_open" and not in_date_group:
                fail("log.group", "Log entries require a preceding date heading (OKF §9)")
            if token.type != "heading_open":
                continue
            in_date_group = False
            if token.tag == "h1":
                continue
            heading = tokens[index + 1].content
            try:
                if token.tag != "h2" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", heading):
                    raise ValueError("Invalid date heading")
                day = date.fromisoformat(heading)
            except ValueError:
                fail("log.date", "Log date headings must be ## YYYY-MM-DD (OKF §9)")
                continue
            if previous is not None and day >= previous:
                fail("log.order", "Log date groups must be unique and newest first (OKF §9)")
            previous = day
            in_date_group = True
    return diagnostics


def check_bundle(root: Path) -> CheckReport:
    """Walk all files without project ignore rules or database initialization."""
    report = CheckReport()
    if not root.is_dir() or root.is_symlink():
        report.diagnostics.append(
            Diagnostic(path=".", rule="bundle.directory", message="Bundle must be a directory")
        )
        return report

    def scan_error(error: OSError) -> None:
        report.diagnostics.append(
            Diagnostic(path=str(error.filename), rule="filesystem.read", message=str(error))
        )

    for directory, directories, files in os.walk(root, onerror=scan_error, followlinks=False):
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                report.diagnostics.append(
                    Diagnostic(
                        path=relative, rule="filesystem.symlink", message="Symlink not portable"
                    )
                )
                continue
            if name not in files or path.suffix != ".md":
                continue
            if not path.is_file():
                report.diagnostics.append(
                    Diagnostic(
                        path=relative,
                        rule="filesystem.regular_file",
                        message="Markdown must be a regular file",
                    )
                )
                continue
            if name not in {"index.md", "log.md"}:
                report.concepts += 1
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                report.diagnostics.append(
                    Diagnostic(path=relative, rule="filesystem.read", message=str(error))
                )
                continue
            report.diagnostics.extend(check_document(relative, content))
    report.diagnostics.sort(key=lambda diagnostic: (diagnostic.path, diagnostic.rule))
    return report
