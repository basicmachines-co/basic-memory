"""Turn one project's observed state into `bm doctor` findings (#1798).

Doctor only reads. Each check below takes a typed observation and returns findings;
the orchestrator at the bottom gathers the observations for one project. Severity
follows the consistency model in AGENTS.md: derived state that is still being worked
on is reported as info, and a finding becomes an error only once nothing will
repair it without an action.
"""

from pathlib import Path
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.index.project_indexing import ProjectIndexObserver
from basic_memory.repository.index_integrity import IndexIntegrityReport, inspect_index_integrity
from basic_memory.repository.note_materialization_health import (
    NoteMaterializationHealth,
    UnsyncedNote,
    inspect_note_materialization,
)
from basic_memory.schemas.doctor import (
    DoctorCheck,
    DoctorFinding,
    DoctorSeverity,
    IndexProjectAction,
    ProjectDoctorReport,
    ReindexSearchAction,
)
from basic_memory.schemas.project_readiness import (
    ProjectIndexPhase,
    ProjectIndexReadiness,
    ProjectIndexStageName,
)
from basic_memory.services.project_readiness import ProjectReadinessService


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def project_path_findings(project_path: Path) -> tuple[DoctorFinding, ...]:
    """A project whose directory is gone cannot be indexed or written."""
    if project_path.is_dir():
        return ()
    return (
        DoctorFinding(
            check=DoctorCheck.PROJECT_PATH,
            severity=DoctorSeverity.ERROR,
            summary=(
                "The project directory does not exist. Restore it, or point the project "
                "at its new location."
            ),
            evidence=(str(project_path),),
        ),
    )


def readiness_findings(readiness: ProjectIndexReadiness) -> tuple[DoctorFinding, ...]:
    """Report a project that was never indexed, or that still has indexing owed."""
    match readiness.phase:
        case ProjectIndexPhase.NEVER_INDEXED if readiness.files_on_disk == 0:
            # A new, empty project has nothing to search yet; that is not a problem.
            return (
                DoctorFinding(
                    check=DoctorCheck.INDEX_READINESS,
                    severity=DoctorSeverity.INFO,
                    summary="The project is empty and has not been indexed yet.",
                ),
            )
        case ProjectIndexPhase.NEVER_INDEXED:
            return (
                DoctorFinding(
                    check=DoctorCheck.INDEX_READINESS,
                    severity=DoctorSeverity.WARNING,
                    summary=(
                        f"The project has never been indexed, so its "
                        f"{_plural(readiness.files_on_disk, 'file')} cannot be searched."
                    ),
                    action=IndexProjectAction(),
                ),
            )
        case ProjectIndexPhase.PENDING:
            pending = ", ".join(
                f"{stage.name} {stage.pending}"
                for stage in readiness.stages
                if stage.phase is ProjectIndexPhase.PENDING
            )
            # Still info: a running Basic Memory server finishes this on its own. The
            # action is for an install with no server running, where nothing will.
            return (
                DoctorFinding(
                    check=DoctorCheck.INDEX_READINESS,
                    severity=DoctorSeverity.INFO,
                    summary=(
                        f"Indexing is still catching up (pending: {pending}). A running "
                        "Basic Memory server finishes this on its own."
                    ),
                    action=IndexProjectAction(),
                ),
            )
        case ProjectIndexPhase.IDLE:
            return ()
        case _ as unreachable:  # pragma: no cover - exhaustiveness proof
            assert_never(unreachable)


def search_index_findings(
    integrity: IndexIntegrityReport,
    files_phase: ProjectIndexPhase,
) -> tuple[DoctorFinding, ...]:
    """Report search rows that are duplicated, orphaned, or missing.

    ``files_phase`` is the files stage of readiness, the stage that writes search
    rows. Pending relations or embeddings do not touch them.
    """
    # Trigger: file indexing still has work owed (or never ran).
    # Why: orphaned and missing search rows are derived state; a pass that is
    #   still running removes or writes them, so reporting them as damage would be
    #   a false alarm.
    # Outcome: they are info until file indexing settles, and errors after it,
    #   because then nothing will repair them without a reindex.
    lag_severity = (
        DoctorSeverity.ERROR if files_phase is ProjectIndexPhase.IDLE else DoctorSeverity.INFO
    )
    findings: list[DoctorFinding] = []
    if integrity.duplicate_keys:
        # SQLite FTS5 enforces no uniqueness, so a duplicate stays until the note's
        # search rows are rewritten. Indexing in progress does not change that.
        findings.append(
            DoctorFinding(
                check=DoctorCheck.SEARCH_INDEX,
                severity=DoctorSeverity.ERROR,
                summary=(
                    f"{_plural(integrity.duplicate_keys, 'indexed item')} "
                    f"{'is' if integrity.duplicate_keys == 1 else 'are'} stored more than once "
                    f"({_plural(integrity.duplicate_rows, 'extra row')}), so search results "
                    "can repeat."
                ),
                evidence=integrity.duplicate_examples,
                action=ReindexSearchAction(full=True),
            )
        )
    if integrity.missing_entity_rows:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.SEARCH_INDEX,
                severity=lag_severity,
                summary=(
                    f"{_plural(integrity.missing_entity_rows, 'note')} "
                    f"{'has' if integrity.missing_entity_rows == 1 else 'have'} no search "
                    "row and cannot be found by search."
                ),
                evidence=integrity.missing_examples,
                action=ReindexSearchAction(full=True),
            )
        )
    if integrity.orphaned_rows:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.SEARCH_INDEX,
                severity=lag_severity,
                summary=(
                    f"{_plural(integrity.orphaned_rows, 'search row')} "
                    f"{'does' if integrity.orphaned_rows == 1 else 'do'} not match a current "
                    "note, observation or relation."
                ),
                evidence=integrity.orphan_examples,
                # A plain reindex only purges rows whose note is gone. A row filed
                # under the wrong live note, or of an unknown kind, survives it; the
                # full pass rewrites every live note's rows and so removes both.
                action=ReindexSearchAction(full=True),
            )
        )
    if integrity.inverted_timestamps:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.SEARCH_INDEX,
                severity=DoctorSeverity.INFO,
                summary=(
                    f"{_plural(integrity.inverted_timestamps, 'note')} "
                    f"{'has' if integrity.inverted_timestamps == 1 else 'have'} a modified "
                    "time before its created time. This comes from file timestamps (for "
                    "example a copied vault) and is harmless."
                ),
            )
        )
    return tuple(findings)


def _unsynced_evidence(notes: tuple[UnsyncedNote, ...]) -> tuple[str, ...]:
    return tuple(
        note.file_path if note.error is None else f"{note.file_path}: {note.error}"
        for note in notes
    )


def materialization_findings(health: NoteMaterializationHealth) -> tuple[DoctorFinding, ...]:
    """Report accepted note writes that have not reached their file."""
    findings: list[DoctorFinding] = []
    if health.external_change_total:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.MATERIALIZATION,
                severity=DoctorSeverity.ERROR,
                summary=(
                    f"{_plural(health.external_change_total, 'note file')} changed on disk "
                    "while a newer write was waiting to be saved, so the write was held "
                    "back to protect the file. Nothing retries it; review these files."
                ),
                evidence=_unsynced_evidence(health.external_change),
            )
        )
    if health.failed_total:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.MATERIALIZATION,
                severity=DoctorSeverity.WARNING,
                summary=(
                    f"{_plural(health.failed_total, 'note')} failed to save to "
                    f"{'its file' if health.failed_total == 1 else 'their files'}. "
                    "The next start retries them; a search reindex retries them now."
                ),
                evidence=_unsynced_evidence(health.failed),
                action=ReindexSearchAction(full=False),
            )
        )
    if health.in_flight:
        findings.append(
            DoctorFinding(
                check=DoctorCheck.MATERIALIZATION,
                severity=DoctorSeverity.INFO,
                summary=f"{_plural(health.in_flight, 'note')} still being saved to disk.",
            )
        )
    return tuple(findings)


async def diagnose_project(
    *,
    project_id: int,
    project_path: Path,
    session_maker: async_sessionmaker[AsyncSession],
    index_observer: ProjectIndexObserver,
    readiness_service: ProjectReadinessService,
) -> ProjectDoctorReport:
    """Run every check for one project and collect the findings."""
    path_findings = project_path_findings(project_path)
    # Trigger: the project directory is missing.
    # Why: readiness walks the directory, and every other check would describe a
    #   project that cannot be indexed until the path is fixed.
    # Outcome: report only the path problem; the rest is meaningful after it is fixed.
    if path_findings:
        return ProjectDoctorReport(findings=path_findings)

    observation = await index_observer.observe_project(project_id)
    readiness = await readiness_service.readiness_for_project_id(
        project_id, observation.observed_files
    )
    integrity = await inspect_index_integrity(session_maker, project_id)
    materialization = await inspect_note_materialization(session_maker, project_id)
    return ProjectDoctorReport(
        findings=(
            *readiness_findings(readiness),
            *search_index_findings(integrity, readiness.stage(ProjectIndexStageName.FILES).phase),
            *materialization_findings(materialization),
        )
    )
