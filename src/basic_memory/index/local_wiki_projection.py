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
class LocalWikiInspection:
    """One deterministic local projection plan and its user-visible state."""

    project_name: str
    project_root: Path
    state: LocalWikiState
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
    project_root = Path(project.path).expanduser().resolve()
    snapshot = await _load_local_wiki_snapshot(
        project,
        project_root=project_root,
        session_maker=session_maker,
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
        project_name=project.name,
        project_root=project_root,
        state=state,
        plan=plan,
    )


async def apply_local_wiki_projection(
    inspection: LocalWikiInspection,
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

    # Every reserved path is checked before the first write. A concurrent edit
    # therefore fails the rebuild without publishing a knowingly mixed projection.
    for write in plan.writes:
        target = inspection.project_root / write.path
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


async def _load_local_wiki_snapshot(
    project: Project,
    *,
    project_root: Path,
    session_maker: async_sessionmaker[AsyncSession],
) -> WikiProjectionSnapshot:
    if not project_root.is_dir():
        raise ValueError(f"Local project directory does not exist: {project_root}")

    parser = EntityParser(project_root)
    source_notes: list[WikiSourceNote] = []
    reserved_documents: list[WikiReservedDocument] = []
    source_modified_at: list[datetime] = []
    scan = scan_local_project_index_files(project_root)
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
        if markdown.modified is not None:
            source_modified_at.append(ensure_timezone_aware(markdown.modified))

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
    source_accepted_at = max(
        (*source_modified_at, *accepted_at, ensure_timezone_aware(project.created_at))
    )
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
