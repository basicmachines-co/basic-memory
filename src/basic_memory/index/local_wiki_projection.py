"""Local filesystem adapter for the deterministic Wiki Projector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Mapping

import frontmatter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import yaml

from basic_memory import db
from basic_memory.file_utils import write_file_atomic_bytes
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.local_project import scan_local_project_index_files
from basic_memory.indexing.wiki_projector import (
    RESERVED_WIKI_FILENAMES,
    WIKI_PROFILE,
    WIKI_PROJECTOR_NAME,
    WIKI_PROJECTOR_VERSION,
    WikiChangeOperation,
    WikiProjectionPlan,
    WikiProjectionReason,
    WikiProjectionRequest,
    WikiProjectionSnapshot,
    WikiReservedDocument,
    WikiSourceChange,
    WikiSourceNote,
    plan_wiki_projection,
)
from basic_memory.markdown import EntityParser
from basic_memory.models import AcceptedProjectNoteChange, Project
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note
from basic_memory.utils import ensure_timezone_aware, generate_permalink


class LocalWikiState(StrEnum):
    """User-visible state of a local Wiki projection."""

    current = "current"
    uninitialized = "uninitialized"
    outdated = "outdated"
    partial = "partial"
    conflicted = "conflicted"


class LocalWikiWriteConflict(RuntimeError):
    """A reserved document changed after the projection was planned."""


@dataclass(frozen=True, slots=True)
class LocalWikiSourceState:
    """Projection inputs revalidated before publishing generated files."""

    project_external_id: str
    project_name: str
    partition_position: int
    accepted_at: datetime
    notes: tuple[WikiSourceNote, ...]
    changes: tuple[WikiSourceChange, ...]
    reserved_documents: tuple[WikiReservedDocument, ...]
    ignore_patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalWikiInspection:
    """One deterministic local projection plan and its user-visible state."""

    project_database_id: int
    project_name: str
    project_root: Path
    state: LocalWikiState
    source_state: LocalWikiSourceState
    plan: WikiProjectionPlan


@dataclass(frozen=True, slots=True)
class AppliedLocalWikiProjection:
    """Reserved documents written by one successful local projection."""

    paths: tuple[str, ...]


async def inspect_local_wiki_projection(
    project: Project,
    *,
    session_maker: async_sessionmaker[AsyncSession],
) -> LocalWikiInspection:
    """Plan a full local Wiki rebuild without modifying canonical files."""
    async with db.scoped_session(session_maker) as session:
        persisted_project = await session.get(Project, project.id)
    if persisted_project is None:
        raise ValueError(f"Local project is not registered: {project.name}")

    project = persisted_project
    configured_root = Path(project.path).expanduser()
    if not configured_root.is_absolute():
        raise ValueError(f"Local Wiki project path must be absolute: {project.path}")
    project_root = configured_root.resolve()
    ignore_patterns = load_gitignore_patterns(project_root)
    snapshot = await _load_local_wiki_snapshot(
        project,
        project_root=project_root,
        session_maker=session_maker,
        ignore_patterns=ignore_patterns,
    )
    plan = plan_wiki_projection(
        WikiProjectionRequest(
            project_id=project.external_id,
            through_partition_position=project.partition_position,
            projector_version=WIKI_PROJECTOR_VERSION,
            reason=WikiProjectionReason.manual_rebuild,
        ),
        snapshot,
    )
    ignored_destinations = tuple(
        write.path
        for write in plan.writes
        if should_ignore_path(project_root / write.path, project_root, ignore_patterns)
    )
    if ignored_destinations:
        raise ValueError(
            "Wiki reserved paths are ignored by project rules: " + ", ".join(ignored_destinations)
        )
    root_initialized = any(
        document.path.casefold() == "index.md" and document.projector_owned
        for document in snapshot.reserved_documents
    )
    if plan.result.conflicts:
        state = LocalWikiState.conflicted
    elif plan.result.pending_materialization:
        state = LocalWikiState.partial
    elif not root_initialized:
        state = LocalWikiState.uninitialized
    elif plan.writes:
        state = LocalWikiState.outdated
    else:
        state = LocalWikiState.current
    return LocalWikiInspection(
        project_database_id=project.id,
        project_name=project.name,
        project_root=project_root,
        state=state,
        source_state=_source_state(snapshot, ignore_patterns=ignore_patterns),
        plan=plan,
    )


async def apply_local_wiki_projection(
    inspection: LocalWikiInspection,
    *,
    session_maker: async_sessionmaker[AsyncSession],
) -> AppliedLocalWikiProjection:
    """Apply an inspected plan with an all-path checksum preflight."""
    plan = inspection.plan
    if plan.result.conflicts:
        raise LocalWikiWriteConflict("Wiki projection has reserved-document conflicts")
    if plan.result.pending_materialization:
        positions = ", ".join(str(position) for position in plan.result.pending_materialization)
        raise LocalWikiWriteConflict(
            f"Wiki projection is waiting for materialized changes: {positions}"
        )

    await _assert_projection_sources_unchanged(
        inspection,
        session_maker=session_maker,
    )

    # Every reserved path is checked before the first write. A concurrent edit
    # therefore fails the rebuild without publishing a knowingly mixed projection.
    for write in plan.writes:
        target = inspection.project_root / write.path
        _require_safe_projection_destination(
            project_root=inspection.project_root,
            target=target,
            relative_path=write.path,
        )
        if write.expected_checksum is None:
            if target.exists():
                raise LocalWikiWriteConflict(
                    f"Wiki reserved path appeared after planning: {write.path}"
                )
            continue
        if not target.is_file():
            raise LocalWikiWriteConflict(
                f"Wiki reserved path disappeared after planning: {write.path}"
            )
        current_checksum = sha256(target.read_bytes()).hexdigest()
        if current_checksum != write.expected_checksum:
            raise LocalWikiWriteConflict(f"Wiki reserved path changed after planning: {write.path}")

    for write in plan.writes:
        target = inspection.project_root / write.path
        target.parent.mkdir(parents=True, exist_ok=True)
        await write_file_atomic_bytes(target, write.content)
    return AppliedLocalWikiProjection(paths=tuple(write.path for write in plan.writes))


def _require_safe_projection_destination(
    *,
    project_root: Path,
    target: Path,
    relative_path: str,
) -> None:
    """Keep every generated write inside the real project directory tree."""
    current = project_root
    for component in target.relative_to(project_root).parts:
        current /= component
        if current.is_symlink():
            raise LocalWikiWriteConflict(f"Wiki reserved path crosses a symlink: {relative_path}")
    if not target.resolve(strict=False).is_relative_to(project_root):
        raise LocalWikiWriteConflict(
            f"Wiki reserved path escapes the project root: {relative_path}"
        )


async def _assert_projection_sources_unchanged(
    inspection: LocalWikiInspection,
    *,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with db.scoped_session(session_maker) as session:
        project = await session.get(Project, inspection.project_database_id)
    if project is None:
        raise LocalWikiWriteConflict("Wiki project disappeared after planning")

    current_root = Path(project.path).expanduser()
    if not current_root.is_absolute() or current_root.resolve() != inspection.project_root:
        raise LocalWikiWriteConflict("Wiki project root changed after planning")

    current_ignore_patterns = load_gitignore_patterns(current_root)
    current_snapshot = await _load_local_wiki_snapshot(
        project,
        project_root=current_root.resolve(),
        session_maker=session_maker,
        ignore_patterns=current_ignore_patterns,
    )
    if (
        _source_state(current_snapshot, ignore_patterns=current_ignore_patterns)
        != inspection.source_state
    ):
        raise LocalWikiWriteConflict("Wiki projection inputs changed after planning")


def _source_state(
    snapshot: WikiProjectionSnapshot,
    *,
    ignore_patterns: set[str],
) -> LocalWikiSourceState:
    """Return only the projection inputs that generated writes depend on."""
    return LocalWikiSourceState(
        project_external_id=snapshot.project_id,
        project_name=snapshot.project_name,
        partition_position=snapshot.source_partition_position,
        accepted_at=snapshot.source_accepted_at,
        notes=snapshot.notes,
        changes=snapshot.changes,
        reserved_documents=snapshot.reserved_documents,
        ignore_patterns=tuple(sorted(ignore_patterns)),
    )


async def _load_local_wiki_snapshot(
    project: Project,
    *,
    project_root: Path,
    session_maker: async_sessionmaker[AsyncSession],
    ignore_patterns: set[str],
) -> WikiProjectionSnapshot:
    if not project_root.is_dir():
        raise ValueError(f"Local project directory does not exist: {project_root}")

    parser = EntityParser(project_root)
    source_notes: list[WikiSourceNote] = []
    reserved_documents: list[WikiReservedDocument] = []
    scan = scan_local_project_index_files(
        project_root,
        ignore_patterns=ignore_patterns,
    )
    if scan.unreadable_directories:
        raise OSError(
            "Local Wiki scan is incomplete; unreadable directories: "
            + ", ".join(scan.unreadable_directories)
        )
    for relative_path in scan.file_paths:
        if not runtime_file_path_is_markdown_note(relative_path):
            continue
        path = project_root / relative_path
        content = path.read_bytes()
        if PurePosixPath(relative_path).name.casefold() in RESERVED_WIKI_FILENAMES:
            reserved_documents.append(
                WikiReservedDocument(
                    path=relative_path,
                    checksum=sha256(content).hexdigest(),
                    content=content,
                    projector_owned=_is_projector_owned(relative_path, content),
                )
            )
            continue

        markdown = await parser.parse_file(path)
        permalink = markdown.frontmatter.permalink or generate_permalink(relative_path)
        source_notes.append(
            WikiSourceNote(
                path=relative_path,
                permalink=permalink,
                title=markdown.frontmatter.title,
                note_type=markdown.frontmatter.type,
                checksum=sha256(content).hexdigest(),
            )
        )
    async with db.scoped_session(session_maker) as session:
        change_rows = (
            await session.execute(
                select(AcceptedProjectNoteChange)
                .where(
                    AcceptedProjectNoteChange.project_id == project.id,
                    AcceptedProjectNoteChange.partition_position <= project.partition_position,
                )
                .order_by(AcceptedProjectNoteChange.partition_position)
            )
        ).scalars()
        changes = tuple(
            WikiSourceChange(
                partition_position=change.partition_position,
                operation=WikiChangeOperation(change.operation),
                path=change.file_path,
                permalink=change.permalink,
                previous_path=change.previous_file_path,
                title=change.title,
                accepted_at=ensure_timezone_aware(change.accepted_at),
                materialized=change.materialized_at is not None,
                source=change.source,
            )
            for change in change_rows
        )

    accepted_at = [change.accepted_at for change in changes]
    source_accepted_at = max((*accepted_at, ensure_timezone_aware(project.created_at)))
    return WikiProjectionSnapshot(
        project_id=project.external_id,
        project_name=project.name,
        source_partition_position=project.partition_position,
        # Local runs have no durable projection ledger. Full rebuilds compare
        # complete rendered bytes, so zero is the honest replay starting point.
        current_output_watermark=0,
        source_accepted_at=source_accepted_at,
        notes=tuple(source_notes),
        changes=changes,
        reserved_documents=tuple(reserved_documents),
    )


def _is_projector_owned(relative_path: str, content: bytes) -> bool:
    # Non-canonical casing is unsafe to rewrite portably: Linux could create a
    # second file while macOS/Windows replace the first one.
    path = PurePosixPath(relative_path)
    if path.name not in RESERVED_WIKI_FILENAMES:
        return False
    try:
        metadata, _ = frontmatter.parse(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return False
    generated = metadata.get("generated")
    basic_memory = metadata.get("bm")
    return (
        isinstance(generated, Mapping)
        and generated.get("by") == WIKI_PROJECTOR_NAME
        and isinstance(basic_memory, Mapping)
        and basic_memory.get("profile") == WIKI_PROFILE
    )
