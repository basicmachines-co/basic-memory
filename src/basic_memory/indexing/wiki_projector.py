"""Deterministic, storage-neutral planning for the Basic Memory Wiki Projector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import PurePosixPath, PureWindowsPath
import unicodedata

OKF_VERSION = "0.2"
WIKI_PROFILE = "wiki/1"
WIKI_PROJECTOR_VERSION = "wiki/1.0.0"
WIKI_PROJECTOR_NAME = "Basic Memory Wiki Projector"
WIKI_PROJECTOR_SOURCE = "wiki_projector"
RESERVED_WIKI_FILENAMES = frozenset({"index.md", "log.md"})
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class WikiProjectionReason(StrEnum):
    """Why a projector run was requested."""

    accepted_note = "accepted_note"
    project_created = "project_created"
    import_rebuild = "import_rebuild"
    manual_rebuild = "manual_rebuild"


class WikiChangeOperation(StrEnum):
    """Accepted note operation represented in generated Wiki logs."""

    created = "created"
    updated = "updated"
    moved = "moved"
    deleted = "deleted"


class WikiProjectionState(StrEnum):
    """User-visible state derived from a projector result or run ledger."""

    current = "current"
    updating = "updating"
    partial = "partial"
    conflicted = "conflicted"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class WikiProjectionRequest:
    """Portable request consumed by local and Cloud projector adapters."""

    project_id: str
    through_partition_position: int
    projector_version: str
    reason: WikiProjectionReason
    requested_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("Wiki projection requires a project_id")
        if self.through_partition_position < 0:
            raise ValueError("Wiki projection position cannot be negative")
        if self.projector_version != WIKI_PROJECTOR_VERSION:
            raise ValueError(f"Wiki projection requires projector_version {WIKI_PROJECTOR_VERSION}")
        normalized_scopes = tuple(
            sorted({_normalize_scope(scope) for scope in self.requested_scopes})
        )
        object.__setattr__(self, "requested_scopes", normalized_scopes)

    @property
    def is_full_rebuild(self) -> bool:
        return self.reason in {
            WikiProjectionReason.import_rebuild,
            WikiProjectionReason.manual_rebuild,
        }


@dataclass(frozen=True, slots=True)
class WikiSourceNote:
    """One accepted, materialized note visible to a projector snapshot."""

    path: str
    permalink: str
    title: str
    note_type: str
    checksum: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normalize_note_path(self.path))
        _validate_canonical_permalink(self.permalink, label=f"Wiki source note {self.path}")
        if not self.title.strip():
            raise ValueError(f"Wiki source note {self.path} requires a title")
        if not self.note_type.strip():
            raise ValueError(f"Wiki source note {self.path} requires a note_type")
        if not self.checksum.strip():
            raise ValueError(f"Wiki source note {self.path} requires a checksum")


@dataclass(frozen=True, slots=True)
class WikiSourceChange:
    """One accepted project-partition change used for materialization-aware logs."""

    partition_position: int
    operation: WikiChangeOperation
    path: str
    permalink: str
    title: str
    accepted_at: datetime
    materialized: bool
    source: str
    previous_path: str | None = None

    def __post_init__(self) -> None:
        if self.partition_position <= 0:
            raise ValueError("Wiki source change position must be positive")
        object.__setattr__(self, "path", _normalize_note_path(self.path))
        _validate_canonical_permalink(self.permalink, label=f"Wiki source change {self.path}")
        if self.previous_path is not None:
            object.__setattr__(self, "previous_path", _normalize_note_path(self.previous_path))
        if not self.title.strip():
            raise ValueError(f"Wiki source change {self.path} requires a title")
        if self.accepted_at.tzinfo is None:
            raise ValueError("Wiki source change accepted_at must be timezone-aware")
        if not self.source.strip():
            raise ValueError("Wiki source change requires a source")


@dataclass(frozen=True, slots=True)
class WikiReservedDocument:
    """Current accepted state for a path reserved to the Wiki Projector."""

    path: str
    checksum: str
    content: bytes
    projector_owned: bool

    def __post_init__(self) -> None:
        normalized_path = _normalize_note_path(self.path)
        if PurePosixPath(normalized_path).name.lower() not in RESERVED_WIKI_FILENAMES:
            raise ValueError(f"Wiki reserved document has non-reserved path: {self.path}")
        if not self.checksum.strip():
            raise ValueError(f"Wiki reserved document {self.path} requires a checksum")
        object.__setattr__(self, "path", normalized_path)


@dataclass(frozen=True, slots=True)
class WikiProjectionSnapshot:
    """Complete deterministic input needed to plan one projector run."""

    project_id: str
    project_name: str
    source_partition_position: int
    current_output_watermark: int
    source_accepted_at: datetime
    notes: tuple[WikiSourceNote, ...]
    changes: tuple[WikiSourceChange, ...]
    reserved_documents: tuple[WikiReservedDocument, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("Wiki projection snapshot requires a project_id")
        if not self.project_name.strip():
            raise ValueError("Wiki projection snapshot requires a project_name")
        if self.source_partition_position < 0:
            raise ValueError("Wiki snapshot source position cannot be negative")
        if self.current_output_watermark < 0:
            raise ValueError("Wiki output watermark cannot be negative")
        if self.source_accepted_at.tzinfo is None:
            raise ValueError("Wiki snapshot source_accepted_at must be timezone-aware")
        _require_unique_paths(self.notes, label="source note")
        _require_unique_paths(
            self.reserved_documents,
            label="reserved document",
            case_sensitive=False,
        )
        positions = [change.partition_position for change in self.changes]
        if len(positions) != len(set(positions)):
            raise ValueError("Wiki source changes require unique partition positions")


@dataclass(frozen=True, slots=True)
class WikiProjectionWrite:
    """Checksum-protected canonical Markdown write planned for an adapter."""

    path: str
    content: bytes
    checksum: str
    expected_checksum: str | None


@dataclass(frozen=True, slots=True)
class WikiProjectionConflict:
    """Reserved path the projector cannot safely claim or replace."""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class WikiProjectionResult:
    """Portable outcome recorded by local and Cloud run ledgers."""

    source_watermark: int
    output_watermark: int
    created: int
    updated: int
    unchanged: int
    conflicts: tuple[WikiProjectionConflict, ...]
    warnings: tuple[str, ...]
    pending_materialization: tuple[int, ...]

    @property
    def state(self) -> WikiProjectionState:
        if self.conflicts:
            return WikiProjectionState.conflicted
        if self.pending_materialization:
            return WikiProjectionState.partial
        if self.output_watermark < self.source_watermark:
            return WikiProjectionState.updating
        return WikiProjectionState.current


@dataclass(frozen=True, slots=True)
class WikiProjectionPlan:
    """Pure projection result plus writes for a runtime adapter to execute."""

    request: WikiProjectionRequest
    writes: tuple[WikiProjectionWrite, ...]
    unchanged_paths: tuple[str, ...]
    result: WikiProjectionResult


def affected_wiki_scopes(*paths: str | None) -> tuple[str, ...]:
    """Return root and every ancestor directory affected by note paths."""
    scopes = {""}
    for path in paths:
        if path is None:
            continue
        parent = PurePosixPath(_normalize_note_path(path)).parent
        while parent != PurePosixPath("."):
            scopes.add(parent.as_posix())
            parent = parent.parent
    return tuple(sorted(scopes))


def plan_wiki_projection(
    request: WikiProjectionRequest,
    snapshot: WikiProjectionSnapshot,
) -> WikiProjectionPlan:
    """Plan deterministic OKF index/log writes without performing I/O."""
    if request.project_id != snapshot.project_id:
        raise ValueError("Wiki projection request and snapshot project_id differ")
    if request.through_partition_position < snapshot.current_output_watermark:
        raise ValueError("Wiki projection request is older than the current output watermark")
    if request.through_partition_position != snapshot.source_partition_position:
        raise ValueError("Wiki projection requires an exact as-of source snapshot")

    changes = tuple(
        sorted(
            (
                change
                for change in snapshot.changes
                if change.partition_position <= request.through_partition_position
                and not _is_projector_change(change)
            ),
            key=lambda change: change.partition_position,
        )
    )
    pending = tuple(
        change.partition_position
        for change in changes
        if not change.materialized and change.partition_position > snapshot.current_output_watermark
    )
    if pending:
        warning = (
            "Projection deferred until accepted note positions are materialized: "
            + ", ".join(str(position) for position in pending)
        )
        return WikiProjectionPlan(
            request=request,
            writes=(),
            unchanged_paths=(),
            result=WikiProjectionResult(
                source_watermark=request.through_partition_position,
                output_watermark=snapshot.current_output_watermark,
                created=0,
                updated=0,
                unchanged=0,
                conflicts=(),
                warnings=(warning,),
                pending_materialization=pending,
            ),
        )

    new_changes = tuple(
        change
        for change in changes
        if change.partition_position > snapshot.current_output_watermark
    )
    projector_only_advance = (
        request.reason == WikiProjectionReason.accepted_note
        and not new_changes
        and any(
            _is_projector_change(change)
            and change.partition_position > snapshot.current_output_watermark
            for change in snapshot.changes
        )
    )
    notes = tuple(
        note
        for note in snapshot.notes
        if PurePosixPath(note.path).name.lower() not in RESERVED_WIKI_FILENAMES
    )
    scopes = _projection_scopes(
        request,
        snapshot,
        notes,
        changes,
        new_changes,
        repair_complete_projection=projector_only_advance,
    )
    all_projection_paths: list[str | None] = [note.path for note in notes]
    all_projection_paths.extend(document.path for document in snapshot.reserved_documents)
    all_projection_paths.extend(change.path for change in changes)
    all_projection_paths.extend(
        change.previous_path for change in changes if change.previous_path is not None
    )
    reserved_permalink_keys = {
        _portable_path_key(_reserved_path(scope, filename).removesuffix(".md"))
        for scope in affected_wiki_scopes(*all_projection_paths)
        for filename in RESERVED_WIKI_FILENAMES
    }
    for note in notes:
        if _portable_path_key(note.permalink) in reserved_permalink_keys:
            raise ValueError(
                "Wiki source note permalink collides with a generated document identity: "
                f"{note.permalink}"
            )
    note_by_path = {_portable_path_key(note.path): note for note in notes}
    scope_by_portable_path: dict[str, str] = {}
    for scope in scopes:
        portable_scope = _portable_path_key(scope)
        if existing_note := note_by_path.get(portable_scope):
            raise ValueError(
                "Wiki projection scope collides with an existing source note path: "
                f"{scope}, {existing_note.path}"
            )
        if existing_scope := scope_by_portable_path.get(portable_scope):
            raise ValueError(
                "Wiki projection scopes must be unique when compared as portable paths: "
                f"{existing_scope}, {scope}"
            )
        scope_by_portable_path[portable_scope] = scope
    existing_by_path = {
        _portable_path_key(document.path): document for document in snapshot.reserved_documents
    }
    rendered: dict[str, bytes] = {}
    for scope in scopes:
        rendered[_reserved_path(scope, "index.md")] = _render_index(
            snapshot=snapshot,
            notes=notes,
            scope=scope,
            source_watermark=request.through_partition_position,
        )
        rendered[_reserved_path(scope, "log.md")] = _render_log(
            snapshot=snapshot,
            changes=changes,
            scope=scope,
            source_watermark=request.through_partition_position,
        )

    conflicts = tuple(
        WikiProjectionConflict(
            path=path,
            reason="reserved path is not owned by the Wiki Projector",
        )
        for path in sorted(rendered)
        if (existing := existing_by_path.get(_portable_path_key(path))) is not None
        and not existing.projector_owned
    )
    if conflicts:
        # Indexes and logs describe one project watermark. Writing only the
        # unblocked paths would publish a mixed projection that no ledger
        # watermark could honestly represent, so conflict is all-or-nothing.
        return WikiProjectionPlan(
            request=request,
            writes=(),
            unchanged_paths=(),
            result=WikiProjectionResult(
                source_watermark=request.through_partition_position,
                output_watermark=snapshot.current_output_watermark,
                created=0,
                updated=0,
                unchanged=0,
                conflicts=conflicts,
                warnings=(),
                pending_materialization=(),
            ),
        )

    writes: list[WikiProjectionWrite] = []
    unchanged_paths: list[str] = []
    created = 0
    updated = 0
    for path, content in sorted(rendered.items()):
        existing = existing_by_path.get(_portable_path_key(path))
        if existing is not None and (
            existing.content == content
            or (
                projector_only_advance
                and _without_projection_metadata(existing.content)
                == _without_projection_metadata(content)
            )
        ):
            unchanged_paths.append(path)
            continue
        writes.append(
            WikiProjectionWrite(
                path=path,
                content=content,
                checksum=sha256(content).hexdigest(),
                expected_checksum=existing.checksum if existing is not None else None,
            )
        )
        if existing is None:
            created += 1
        else:
            updated += 1

    return WikiProjectionPlan(
        request=request,
        writes=tuple(writes),
        unchanged_paths=tuple(unchanged_paths),
        result=WikiProjectionResult(
            source_watermark=request.through_partition_position,
            output_watermark=request.through_partition_position,
            created=created,
            updated=updated,
            unchanged=len(unchanged_paths),
            conflicts=(),
            warnings=(),
            pending_materialization=(),
        ),
    )


def _projection_scopes(
    request: WikiProjectionRequest,
    snapshot: WikiProjectionSnapshot,
    notes: tuple[WikiSourceNote, ...],
    changes: tuple[WikiSourceChange, ...],
    new_changes: tuple[WikiSourceChange, ...],
    *,
    repair_complete_projection: bool,
) -> tuple[str, ...]:
    if request.is_full_rebuild or repair_complete_projection:
        paths = [note.path for note in notes]
        paths.extend(document.path for document in snapshot.reserved_documents)
        paths.extend(change.path for change in changes)
        paths.extend(change.previous_path for change in changes if change.previous_path is not None)
        return affected_wiki_scopes(*paths)
    paths = [change.path for change in new_changes]
    paths.extend(change.previous_path for change in new_changes if change.previous_path is not None)
    scopes = set(affected_wiki_scopes(*paths))
    for requested_scope in request.requested_scopes:
        scope = PurePosixPath(requested_scope)
        while scope != PurePosixPath("."):
            scopes.add(scope.as_posix())
            scope = scope.parent
    return tuple(sorted(scopes))


def _without_projection_metadata(content: bytes) -> bytes:
    frontmatter, separator, body = content.partition(b"\n---\n")
    normalized_frontmatter = b"\n".join(
        b"  at:"
        if line.startswith(b"  at: ")
        else b"  source_watermark:"
        if line.startswith(b"  source_watermark: ")
        else line
        for line in frontmatter.split(b"\n")
    )
    return normalized_frontmatter + separator + body


def _render_index(
    *,
    snapshot: WikiProjectionSnapshot,
    notes: tuple[WikiSourceNote, ...],
    scope: str,
    source_watermark: int,
) -> bytes:
    direct_notes = sorted(
        (note for note in notes if _parent_scope(note.path) == scope),
        key=lambda note: (
            note.title.casefold(),
            note.path.casefold(),
            note.title,
            note.path,
        ),
    )
    child_scope_set: set[str] = set()
    for note in notes:
        if not _is_descendant(note.path, scope):
            continue
        child_scope = _direct_child_scope(scope, note.path)
        if child_scope is not None:
            child_scope_set.add(child_scope)
    child_scopes = sorted(child_scope_set)
    title = snapshot.project_name if not scope else _display_name(PurePosixPath(scope).name)
    body: list[str] = [f"# {_escape_generated_markdown_text(title)}", ""]
    if child_scopes:
        body.extend(["## Sections", ""])
        body.extend(
            "- "
            f"[[{child_scope}/index|"
            f"{_escape_generated_markdown_text(_display_name(PurePosixPath(child_scope).name))}]]"
            for child_scope in child_scopes
        )
        body.append("")
    if direct_notes:
        body.extend(["## Notes", ""])
        body.extend(
            f"- [[{note.permalink}|{_escape_generated_markdown_text(note.title)}]]"
            for note in direct_notes
        )
        body.append("")
    if not child_scopes and not direct_notes:
        body.extend(["No concepts have been projected into this scope yet.", ""])
    return _render_document(
        note_type="Index",
        title=title,
        source_watermark=source_watermark,
        generated_at=snapshot.source_accepted_at,
        body="\n".join(body),
        include_okf_version=not scope,
    )


def _render_log(
    *,
    snapshot: WikiProjectionSnapshot,
    changes: tuple[WikiSourceChange, ...],
    scope: str,
    source_watermark: int,
) -> bytes:
    relevant = tuple(
        sorted(
            (
                change
                for change in changes
                if change.materialized
                and (
                    _is_descendant(change.path, scope)
                    or (
                        change.previous_path is not None
                        and _is_descendant(change.previous_path, scope)
                    )
                )
            ),
            key=lambda change: change.partition_position,
            reverse=True,
        )
    )
    title = (
        f"{snapshot.project_name} log"
        if not scope
        else f"{_display_name(PurePosixPath(scope).name)} log"
    )
    body: list[str] = [f"# {_escape_generated_markdown_text(title)}", ""]
    if relevant:
        body.extend(_render_log_entry(change) for change in relevant)
        body.append("")
    else:
        body.extend(["No accepted materialized changes have been recorded yet.", ""])
    return _render_document(
        note_type="Log",
        title=title,
        source_watermark=source_watermark,
        generated_at=snapshot.source_accepted_at,
        body="\n".join(body),
        include_okf_version=False,
    )


def _render_log_entry(change: WikiSourceChange) -> str:
    timestamp = _isoformat_utc(change.accepted_at)
    title = _escape_generated_markdown_text(change.title)
    current_note = f"[[{change.permalink}|{title}]]"
    match change.operation:
        case WikiChangeOperation.created:
            description = f"Created {current_note}"
        case WikiChangeOperation.updated:
            description = f"Updated {current_note}"
        case WikiChangeOperation.moved:
            if change.previous_path is None:
                raise ValueError("Moved Wiki change requires previous_path")
            description = f"Moved `{change.previous_path}` to {current_note}"
        case WikiChangeOperation.deleted:
            description = f"Deleted `{change.path}`"
    return f"- {timestamp} — {description}"


def _render_document(
    *,
    note_type: str,
    title: str,
    source_watermark: int,
    generated_at: datetime,
    body: str,
    include_okf_version: bool,
) -> bytes:
    frontmatter = ["---", f"type: {note_type}"]
    if include_okf_version:
        frontmatter.append(f'okf_version: "{OKF_VERSION}"')
    frontmatter.extend(
        [
            f"title: {json.dumps(title, ensure_ascii=False)}",
            "generated:",
            f"  by: {WIKI_PROJECTOR_NAME}",
            f"  at: {json.dumps(_isoformat_utc(generated_at))}",
            "bm:",
            f"  profile: {WIKI_PROFILE}",
            f'  source_watermark: "{source_watermark}"',
            "---",
            body,
        ]
    )
    return ("\n".join(frontmatter).rstrip() + "\n").encode("utf-8")


def _is_projector_change(change: WikiSourceChange) -> bool:
    return (
        change.source == WIKI_PROJECTOR_SOURCE
        and PurePosixPath(change.path).name.lower() in RESERVED_WIKI_FILENAMES
    )


def _normalize_note_path(path: str) -> str:
    normalized = _normalize_relative_path(path)
    if not normalized or PurePosixPath(normalized).suffix.lower() != ".md":
        raise ValueError(f"Wiki note path must be project-relative Markdown: {path}")
    if "::" in normalized or any(character in normalized for character in "\r\n[]|`<>"):
        raise ValueError(f"Wiki note path contains unsupported Markdown delimiters: {path}")
    _validate_portable_path_components(normalized, path_kind="note path", source=path)
    _reject_reserved_wiki_directory_components(
        PurePosixPath(normalized).parts[:-1],
        path_kind="note path",
        source=path,
    )
    return normalized


def _validate_canonical_permalink(permalink: str, *, label: str) -> None:
    if not permalink.strip() or permalink != permalink.strip():
        raise ValueError(f"{label} requires a canonical permalink")
    if "::" in permalink or any(character in permalink for character in "\r\n[]|`<>"):
        raise ValueError(f"{label} has an unsafe canonical permalink")


def _normalize_scope(scope: str) -> str:
    normalized = _normalize_relative_path(scope)
    if not normalized:
        return ""
    if "::" in normalized or any(character in normalized for character in "\x00\r\n[]|`<>"):
        raise ValueError(f"Wiki scope contains unsupported Markdown delimiters: {scope}")
    _validate_portable_path_components(normalized, path_kind="scope", source=scope)
    _reject_reserved_wiki_directory_components(
        PurePosixPath(normalized).parts,
        path_kind="scope",
        source=scope,
    )
    return normalized


def _reject_reserved_wiki_directory_components(
    components: tuple[str, ...],
    *,
    path_kind: str,
    source: str,
) -> None:
    if any(component.casefold() in RESERVED_WIKI_FILENAMES for component in components):
        raise ValueError(f"Wiki {path_kind} contains a reserved Wiki directory name: {source}")


def _validate_portable_path_components(
    normalized: str,
    *,
    path_kind: str,
    source: str,
) -> None:
    for component in PurePosixPath(normalized).parts:
        if any(unicodedata.category(character) == "Cc" for character in component):
            raise ValueError(f"Wiki {path_kind} contains a control character: {source}")
        if any(character in component for character in ':"?*'):
            raise ValueError(f"Wiki {path_kind} contains a Windows-invalid character: {source}")
        if component.endswith((".", " ")):
            raise ValueError(f"Wiki {path_kind} contains a non-portable path component: {source}")
        stem = component.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"Wiki {path_kind} contains a reserved device name: {source}")


def _normalize_relative_path(path: str) -> str:
    if path != path.strip():
        raise ValueError(f"Wiki path must not contain boundary whitespace: {path}")
    accepted_path = path
    windows_path = PureWindowsPath(accepted_path)
    if accepted_path.startswith(("/", "\\")) or windows_path.drive or windows_path.is_absolute():
        raise ValueError(f"Wiki path must be project-relative and normalized: {path}")
    candidate = accepted_path.replace("\\", "/")
    if not candidate:
        return ""
    if candidate.endswith("/"):
        raise ValueError(f"Wiki path must be project-relative and normalized: {path}")
    parsed = PurePosixPath(candidate)
    if (
        parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != candidate
    ):
        raise ValueError(f"Wiki path must be project-relative and normalized: {path}")
    return parsed.as_posix()


def _escape_generated_markdown_text(value: str) -> str:
    """Keep snapshot metadata from changing generated Markdown structure."""
    return value.translate(
        str.maketrans(
            {
                "\r": " ",
                "\n": " ",
                "&": "&amp;",
                "\\": "&#92;",
                "[": "&#91;",
                "]": "&#93;",
                "|": "&#124;",
                "`": "&#96;",
                "<": "&lt;",
                ">": "&gt;",
            }
        )
    )


def _require_unique_paths(
    values: tuple[object, ...],
    *,
    label: str,
    case_sensitive: bool = True,
) -> None:
    paths = [getattr(value, "path") for value in values]
    if not case_sensitive:
        paths = [_portable_path_key(path) for path in paths]
    if len(paths) != len(set(paths)):
        raise ValueError(f"Wiki projection snapshot has duplicate {label} paths")


def _portable_path_key(path: str) -> str:
    """Compare paths the way normalization-insensitive filesystems do."""
    return unicodedata.normalize("NFC", path).casefold()


def _reserved_path(scope: str, filename: str) -> str:
    return f"{scope}/{filename}" if scope else filename


def _parent_scope(path: str) -> str:
    parent = PurePosixPath(path).parent
    return "" if parent == PurePosixPath(".") else parent.as_posix()


def _is_descendant(path: str, scope: str) -> bool:
    if not scope:
        return True
    return path == scope or path.startswith(f"{scope}/")


def _direct_child_scope(scope: str, note_path: str) -> str | None:
    note_parent = _parent_scope(note_path)
    if not note_parent or note_parent == scope:
        return None
    prefix = f"{scope}/" if scope else ""
    child_name = note_parent[len(prefix) :].split("/", maxsplit=1)[0]
    return f"{scope}/{child_name}" if scope else child_name


def _display_name(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip().title()


def _isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
