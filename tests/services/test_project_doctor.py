"""Doctor findings for each observed project state (#1798)."""

from pathlib import Path

import pytest

from basic_memory.repository.index_integrity import IndexIntegrityReport
from basic_memory.repository.note_materialization_health import (
    NoteMaterializationHealth,
    UnsyncedNote,
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
    ProjectIndexStage,
    ProjectIndexStageName,
)
from basic_memory.services.project_doctor import (
    materialization_findings,
    project_path_findings,
    readiness_findings,
    search_index_findings,
)


def _readiness(
    phase: ProjectIndexPhase,
    *,
    files_on_disk: int = 3,
    pending: dict[ProjectIndexStageName, int] | None = None,
) -> ProjectIndexReadiness:
    pending = pending or {}
    return ProjectIndexReadiness(
        phase=phase,
        files_on_disk=files_on_disk,
        indexed_entities=files_on_disk,
        stages=tuple(
            ProjectIndexStage(
                name=name,
                phase=(
                    ProjectIndexPhase.PENDING
                    if pending.get(name)
                    else ProjectIndexPhase.IDLE
                    if phase is ProjectIndexPhase.PENDING
                    else phase
                ),
                pending=pending.get(name, 0),
                total=files_on_disk,
            )
            for name in ProjectIndexStageName
        ),
    )


def _integrity(**anomalies: int | tuple[str, ...]) -> IndexIntegrityReport:
    fields: dict[str, int | tuple[str, ...]] = {
        "entities": 3,
        "search_rows": 3,
        "entity_rows": 3,
        "observation_rows": 0,
        "relation_rows": 0,
        "duplicate_keys": 0,
        "duplicate_rows": 0,
        "duplicate_examples": (),
        "orphaned_rows": 0,
        "orphan_examples": (),
        "missing_entity_rows": 0,
        "missing_examples": (),
        "inverted_timestamps": 0,
    }
    fields.update(anomalies)
    return IndexIntegrityReport(**fields)  # pyright: ignore[reportArgumentType]


def _health(**unsynced: int | tuple[UnsyncedNote, ...]) -> NoteMaterializationHealth:
    fields: dict[str, int | tuple[UnsyncedNote, ...]] = {
        "in_flight": 0,
        "failed": (),
        "failed_total": 0,
        "external_change": (),
        "external_change_total": 0,
    }
    fields.update(unsynced)
    return NoteMaterializationHealth(**fields)  # pyright: ignore[reportArgumentType]


# --- Project path ---


def test_existing_project_directory_has_no_findings(tmp_path: Path) -> None:
    assert project_path_findings(tmp_path) == ()


def test_missing_project_directory_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    (finding,) = project_path_findings(missing)
    assert finding.check is DoctorCheck.PROJECT_PATH
    assert finding.severity is DoctorSeverity.ERROR
    assert finding.evidence == (str(missing),)
    assert finding.action is None


# --- Index readiness ---


def test_settled_index_has_no_findings() -> None:
    assert readiness_findings(_readiness(ProjectIndexPhase.IDLE)) == ()


def test_never_indexed_project_with_files_suggests_indexing() -> None:
    (finding,) = readiness_findings(_readiness(ProjectIndexPhase.NEVER_INDEXED, files_on_disk=2))
    assert finding.severity is DoctorSeverity.WARNING
    assert "2 files cannot be searched" in finding.summary
    assert finding.action == IndexProjectAction()


def test_new_empty_project_is_only_info() -> None:
    (finding,) = readiness_findings(_readiness(ProjectIndexPhase.NEVER_INDEXED, files_on_disk=0))
    assert finding.severity is DoctorSeverity.INFO
    assert finding.action is None


def test_pending_index_is_info_and_names_each_stage() -> None:
    readiness = _readiness(
        ProjectIndexPhase.PENDING,
        pending={ProjectIndexStageName.FILES: 2, ProjectIndexStageName.EMBEDDINGS: 5},
    )
    (finding,) = readiness_findings(readiness)
    assert finding.severity is DoctorSeverity.INFO
    assert finding.summary.startswith(
        "Indexing is still catching up (pending: files 2, embeddings 5)."
    )
    # Offered for an install with no server running, where nothing else will index.
    assert finding.action == IndexProjectAction()


# --- Search index ---


def test_healthy_search_index_has_no_findings() -> None:
    assert search_index_findings(_integrity(), ProjectIndexPhase.IDLE) == ()


def test_duplicates_suggest_a_full_reindex() -> None:
    (finding,) = search_index_findings(
        _integrity(duplicate_keys=1, duplicate_rows=2, duplicate_examples=("entity id=1: 3",)),
        ProjectIndexPhase.IDLE,
    )
    assert finding.summary == (
        "1 indexed item is stored more than once (2 extra rows), so search results can repeat."
    )
    assert finding.evidence == ("entity id=1: 3",)
    assert finding.action == ReindexSearchAction(full=True)


@pytest.mark.parametrize(
    ("files_phase", "severity"),
    [
        (ProjectIndexPhase.IDLE, DoctorSeverity.ERROR),
        (ProjectIndexPhase.PENDING, DoctorSeverity.WARNING),
        (ProjectIndexPhase.NEVER_INDEXED, DoctorSeverity.WARNING),
    ],
)
def test_search_row_anomalies_are_errors_only_once_file_indexing_settles(
    files_phase: ProjectIndexPhase, severity: DoctorSeverity
) -> None:
    duplicate, missing, orphaned = search_index_findings(
        _integrity(
            duplicate_keys=1,
            duplicate_rows=1,
            missing_entity_rows=2,
            missing_examples=("a.md", "b.md"),
            orphaned_rows=1,
            orphan_examples=("entity id=9, entity_id=9",),
        ),
        files_phase,
    )
    # An owed pass may or may not rewrite the affected note, so doctor claims neither.
    assert duplicate.severity is missing.severity is orphaned.severity is severity
    still_running = files_phase is not ProjectIndexPhase.IDLE
    for finding in (duplicate, missing, orphaned):
        assert ("may clear this; if it remains, run the fix" in finding.summary) is still_running
    assert missing.summary.startswith("2 notes have no search row and cannot be found by search.")
    assert missing.action == ReindexSearchAction(full=True)
    assert orphaned.summary.startswith(
        "1 search row does not match a current note, observation or relation."
    )
    assert orphaned.action == ReindexSearchAction(full=True)


def test_singular_and_plural_wording() -> None:
    missing, orphaned = search_index_findings(
        _integrity(missing_entity_rows=1, orphaned_rows=2), ProjectIndexPhase.IDLE
    )
    assert missing.summary.startswith("1 note has no search row")
    assert orphaned.summary.startswith("2 search rows do not match")


@pytest.mark.parametrize(("count", "verb"), [(1, "has"), (4, "have")])
def test_inverted_timestamps_are_info_without_an_action(count: int, verb: str) -> None:
    (finding,) = search_index_findings(
        _integrity(inverted_timestamps=count), ProjectIndexPhase.IDLE
    )
    assert finding.severity is DoctorSeverity.INFO
    assert f" {verb} a modified time before its created time" in finding.summary
    assert finding.action is None


# --- Materialization ---


def test_synced_notes_have_no_findings() -> None:
    assert materialization_findings(_health()) == ()


def test_external_changes_are_errors_with_no_automatic_fix() -> None:
    (finding,) = materialization_findings(
        _health(
            external_change=(UnsyncedNote(file_path="notes/a.md", error=None),),
            external_change_total=1,
        )
    )
    assert finding.check is DoctorCheck.MATERIALIZATION
    assert finding.severity is DoctorSeverity.ERROR
    assert finding.summary.startswith("1 note file changed on disk")
    assert finding.evidence == ("notes/a.md",)
    assert finding.action is None


@pytest.mark.parametrize(("total", "phrase"), [(1, "its file"), (2, "their files")])
def test_failed_writes_are_warnings_retried_by_a_search_reindex(total: int, phrase: str) -> None:
    (finding,) = materialization_findings(
        _health(
            failed=(UnsyncedNote(file_path="notes/b.md", error="disk full"),),
            failed_total=total,
        )
    )
    assert finding.severity is DoctorSeverity.WARNING
    assert f"failed to save to {phrase}" in finding.summary
    assert finding.evidence == ("notes/b.md: disk full",)
    assert finding.action == ReindexSearchAction(full=False)


def test_writes_in_flight_are_info() -> None:
    (finding,) = materialization_findings(_health(in_flight=3))
    assert finding.severity is DoctorSeverity.INFO
    assert finding.summary == "3 notes still being saved to disk."


# --- Report ---


def test_report_has_errors_only_for_error_findings() -> None:
    def finding(severity: DoctorSeverity) -> DoctorFinding:
        return DoctorFinding(check=DoctorCheck.SEARCH_INDEX, severity=severity, summary="x")

    assert not ProjectDoctorReport(findings=()).has_errors
    assert not ProjectDoctorReport(
        findings=(finding(DoctorSeverity.INFO), finding(DoctorSeverity.WARNING))
    ).has_errors
    assert ProjectDoctorReport(findings=(finding(DoctorSeverity.ERROR),)).has_errors
