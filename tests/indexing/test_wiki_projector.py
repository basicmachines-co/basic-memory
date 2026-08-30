"""Deterministic Wiki Projector contract and byte-output tests."""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from basic_memory.indexing.wiki_projector import (
    WikiChangeOperation,
    WikiProjectionReason,
    WikiProjectionRequest,
    WikiProjectionResult,
    WikiProjectionSnapshot,
    WikiProjectionState,
    WIKI_PROJECTOR_VERSION,
    WikiReservedDocument,
    WikiSourceChange,
    WikiSourceNote,
    affected_wiki_scopes,
    plan_wiki_projection,
)

ACCEPTED_AT = datetime(2026, 8, 29, 18, 30, tzinfo=timezone.utc)


def _request(
    *,
    position: int = 3,
    reason: WikiProjectionReason = WikiProjectionReason.accepted_note,
    scopes: tuple[str, ...] = ("guides",),
) -> WikiProjectionRequest:
    return WikiProjectionRequest(
        project_id="project-88",
        through_partition_position=position,
        projector_version=WIKI_PROJECTOR_VERSION,
        reason=reason,
        requested_scopes=scopes,
    )


def _snapshot(
    *,
    output_watermark: int = 2,
    materialized: bool = True,
    reserved_documents: tuple[WikiReservedDocument, ...] = (),
) -> WikiProjectionSnapshot:
    return WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=3,
        current_output_watermark=output_watermark,
        source_accepted_at=ACCEPTED_AT,
        notes=(
            WikiSourceNote(
                path="overview.md",
                title="Overview",
                note_type="Note",
                checksum="overview-checksum",
            ),
            WikiSourceNote(
                path="guides/setup.md",
                title="Setup",
                note_type="Guide",
                checksum="setup-checksum",
            ),
            WikiSourceNote(
                path="guides/deep/details.md",
                title="Details",
                note_type="Guide",
                checksum="details-checksum",
            ),
        ),
        changes=(
            WikiSourceChange(
                partition_position=3,
                operation=WikiChangeOperation.updated,
                path="guides/setup.md",
                title="Setup",
                accepted_at=ACCEPTED_AT,
                materialized=materialized,
                source="web",
            ),
        ),
        reserved_documents=reserved_documents,
    )


def _reserved(path: str, content: bytes, *, owned: bool = True) -> WikiReservedDocument:
    return WikiReservedDocument(
        path=path,
        checksum=sha256(content).hexdigest(),
        content=content,
        projector_owned=owned,
    )


def test_affected_scopes_include_root_and_move_ancestors() -> None:
    assert affected_wiki_scopes("guides/old/setup.md", "reference/new/setup.md") == (
        "",
        "guides",
        "guides/old",
        "reference",
        "reference/new",
    )


def test_affected_scopes_ignore_missing_paths() -> None:
    assert affected_wiki_scopes(None) == ("",)


def test_projection_request_rejects_invalid_contract_fields() -> None:
    request = _request()

    with pytest.raises(ValueError, match="requires a project_id"):
        replace(request, project_id=" ")
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(request, through_partition_position=-1)
    with pytest.raises(ValueError, match="requires projector_version wiki/1.0.0"):
        replace(request, projector_version=" ")
    with pytest.raises(ValueError, match="requires projector_version wiki/1.0.0"):
        replace(request, projector_version="wiki/2.0.0")


def test_projection_request_normalizes_and_deduplicates_scopes() -> None:
    request = _request(scopes=("guides\\deep", "guides/deep", ""))

    assert request.requested_scopes == ("", "guides/deep")


@pytest.mark.parametrize(
    "scope",
    (
        "bad|scope",
        "bad::scope",
        "bad\nscope",
        "bad\x00scope",
        "bad\x01scope",
        "bad\x7fscope",
        "bad:scope",
        "CON",
        "guides/trailing.",
        " guides",
        "guides ",
    ),
)
def test_projection_request_rejects_nonportable_scopes(scope: str) -> None:
    with pytest.raises(ValueError, match="Wiki (path|scope)"):
        _request(scopes=(scope,))


def test_source_note_rejects_missing_metadata() -> None:
    note = WikiSourceNote(
        path="note.md",
        title="Note",
        note_type="Note",
        checksum="checksum",
    )

    with pytest.raises(ValueError, match="requires a title"):
        replace(note, title=" ")
    with pytest.raises(ValueError, match="requires a note_type"):
        replace(note, note_type=" ")
    with pytest.raises(ValueError, match="requires a checksum"):
        replace(note, checksum=" ")


@pytest.mark.parametrize(
    "path",
    (
        "bad?/note.md",
        "bad\x01/note.md",
        "bad\x7f/note.md",
        "NUL/note.md",
        "trailing./note.md",
        " note.md",
        "note.md ",
    ),
)
def test_source_note_rejects_nonportable_path_components(path: str) -> None:
    with pytest.raises(ValueError, match="Wiki (path|note path)"):
        WikiSourceNote(
            path=path,
            title="Note",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize("path", ("index.md/note.md", "guides/LOG.md/note.md"))
def test_source_note_rejects_reserved_wiki_directory_components(path: str) -> None:
    with pytest.raises(ValueError, match="reserved Wiki directory name"):
        WikiSourceNote(
            path=path,
            title="Note",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize("scope", ("index.md", "guides/LOG.md"))
def test_request_rejects_reserved_wiki_directory_scopes(scope: str) -> None:
    with pytest.raises(ValueError, match="reserved Wiki directory name"):
        _request(scopes=(scope,))


def test_source_change_rejects_invalid_contract_fields() -> None:
    change = WikiSourceChange(
        partition_position=1,
        operation=WikiChangeOperation.updated,
        path="note.md",
        title="Note",
        accepted_at=ACCEPTED_AT,
        materialized=True,
        source="web",
    )

    with pytest.raises(ValueError, match="position must be positive"):
        replace(change, partition_position=0)
    with pytest.raises(ValueError, match="requires a title"):
        replace(change, title=" ")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(change, accepted_at=ACCEPTED_AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="requires a source"):
        replace(change, source=" ")


def test_source_change_normalizes_previous_path() -> None:
    change = WikiSourceChange(
        partition_position=1,
        operation=WikiChangeOperation.moved,
        path="new/note.md",
        previous_path="old\\note.md",
        title="Note",
        accepted_at=ACCEPTED_AT,
        materialized=True,
        source="web",
    )

    assert change.previous_path == "old/note.md"


def test_reserved_document_requires_a_reserved_path_and_checksum() -> None:
    with pytest.raises(ValueError, match="non-reserved path"):
        _reserved("note.md", b"content")
    with pytest.raises(ValueError, match="requires a checksum"):
        WikiReservedDocument(
            path="index.md",
            checksum=" ",
            content=b"content",
            projector_owned=True,
        )


def test_snapshot_rejects_invalid_contract_fields() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="requires a project_id"):
        replace(snapshot, project_id=" ")
    with pytest.raises(ValueError, match="requires a project_name"):
        replace(snapshot, project_name=" ")
    with pytest.raises(ValueError, match="source position cannot be negative"):
        replace(snapshot, source_partition_position=-1)
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(snapshot, current_output_watermark=-1)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(snapshot, source_accepted_at=ACCEPTED_AT.replace(tzinfo=None))


def test_snapshot_rejects_duplicate_note_paths_and_change_positions() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="duplicate source note paths"):
        replace(snapshot, notes=(snapshot.notes[0], snapshot.notes[0]))
    with pytest.raises(ValueError, match="unique partition positions"):
        replace(snapshot, changes=(snapshot.changes[0], snapshot.changes[0]))


def test_snapshot_rejects_case_folded_duplicate_reserved_paths() -> None:
    lower = _reserved("guides/index.md", b"lower")
    upper = _reserved("guides/Index.md", b"upper")

    with pytest.raises(ValueError, match="duplicate reserved document paths"):
        replace(_snapshot(), reserved_documents=(lower, upper))


@pytest.mark.parametrize(
    ("first_scope", "second_scope"),
    (("Foo", "foo"), ("caf\u00e9", "cafe\u0301")),
)
def test_projection_rejects_nonportable_duplicate_scopes(
    first_scope: str,
    second_scope: str,
) -> None:
    snapshot = replace(
        _snapshot(),
        notes=(
            WikiSourceNote(
                path=f"{first_scope}/one.md",
                title="One",
                note_type="Note",
                checksum="one-checksum",
            ),
            WikiSourceNote(
                path=f"{second_scope}/two.md",
                title="Two",
                note_type="Note",
                checksum="two-checksum",
            ),
        ),
    )

    with pytest.raises(ValueError, match="unique when compared as portable paths"):
        plan_wiki_projection(
            _request(reason=WikiProjectionReason.manual_rebuild, scopes=()),
            snapshot,
        )


def test_snapshot_rejects_unicode_normalized_duplicate_reserved_paths() -> None:
    composed = _reserved("caf\u00e9/index.md", b"composed")
    decomposed = _reserved("cafe\u0301/index.md", b"decomposed")

    with pytest.raises(ValueError, match="duplicate reserved document paths"):
        replace(_snapshot(), reserved_documents=(composed, decomposed))


def test_projection_rejects_scope_that_collides_with_source_note_path() -> None:
    with pytest.raises(ValueError, match="collides with an existing source note path"):
        plan_wiki_projection(_request(scopes=("overview.md",)), _snapshot())


def test_projection_result_reports_updating_when_output_lags_source() -> None:
    result = WikiProjectionResult(
        source_watermark=3,
        output_watermark=2,
        created=0,
        updated=0,
        unchanged=0,
        conflicts=(),
        warnings=(),
        pending_materialization=(),
    )

    assert result.state == WikiProjectionState.updating


def test_projection_rejects_mismatched_project_and_stale_request() -> None:
    with pytest.raises(ValueError, match="project_id differ"):
        plan_wiki_projection(_request(), replace(_snapshot(), project_id="other"))
    with pytest.raises(ValueError, match="older than"):
        plan_wiki_projection(_request(position=2), _snapshot(output_watermark=3))


def test_projection_renders_root_and_affected_directory_indexes_and_logs() -> None:
    plan = plan_wiki_projection(_request(), _snapshot())

    assert [write.path for write in plan.writes] == [
        "guides/index.md",
        "guides/log.md",
        "index.md",
        "log.md",
    ]
    rendered = {write.path: write.content.decode() for write in plan.writes}
    assert "[[guides/deep/index|Deep]]" in rendered["guides/index.md"]
    assert "[[guides/setup|Setup]]" in rendered["guides/index.md"]
    assert "[[guides/index|Guides]]" in rendered["index.md"]
    assert "[[overview|Overview]]" in rendered["index.md"]
    assert "Updated [[guides/setup|Setup]]" in rendered["guides/log.md"]
    assert plan.result.source_watermark == 3
    assert plan.result.output_watermark == 3
    assert plan.result.created == 4
    assert plan.result.state == WikiProjectionState.current


def test_projection_bytes_match_the_shared_contract_fixture() -> None:
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "wiki_projector" / "basic_projection.json"
    )
    fixture = json.loads(fixture_path.read_text())

    plan = plan_wiki_projection(_request(), _snapshot())

    assert fixture["contract_version"] == plan.request.projector_version
    assert fixture["project_id"] == plan.request.project_id
    assert fixture["through_partition_position"] == plan.request.through_partition_position
    assert fixture["expected_sha256"] == {write.path: write.checksum for write in plan.writes}


def test_projection_is_a_byte_identical_noop_at_the_same_watermark() -> None:
    first = plan_wiki_projection(_request(), _snapshot())
    existing = tuple(_reserved(write.path, write.content) for write in first.writes)

    replay = plan_wiki_projection(
        _request(),
        _snapshot(output_watermark=3, reserved_documents=existing),
    )

    assert replay.writes == ()
    assert replay.unchanged_paths == tuple(write.path for write in first.writes)
    assert replay.result.unchanged == 4
    assert replay.result.state == WikiProjectionState.current


def test_full_rebuild_records_unchanged_and_updated_reserved_documents() -> None:
    request = _request(reason=WikiProjectionReason.manual_rebuild, scopes=())
    first = plan_wiki_projection(request, _snapshot())
    first_by_path = {write.path: write for write in first.writes}
    unchanged = first_by_path["index.md"]
    stale = _reserved("log.md", b"stale\n")
    snapshot = _snapshot(
        reserved_documents=(
            _reserved(unchanged.path, unchanged.content),
            stale,
        )
    )

    replay = plan_wiki_projection(request, snapshot)

    assert replay.unchanged_paths == ("index.md",)
    assert replay.result.unchanged == 1
    assert replay.result.updated == 1
    updated_log = next(write for write in replay.writes if write.path == "log.md")
    assert updated_log.expected_checksum == stale.checksum


def test_pending_materialization_defers_all_bytes_without_advancing_output() -> None:
    plan = plan_wiki_projection(_request(), _snapshot(materialized=False))

    assert plan.writes == ()
    assert plan.result.output_watermark == 2
    assert plan.result.pending_materialization == (3,)
    assert plan.result.state == WikiProjectionState.partial


def test_user_claimed_reserved_path_is_a_conflict_not_a_write() -> None:
    claimed = _reserved("guides/index.md", b"# User index\n", owned=False)

    plan = plan_wiki_projection(
        _request(),
        _snapshot(reserved_documents=(claimed,)),
    )

    assert plan.writes == ()
    assert plan.result.conflicts[0].path == "guides/index.md"
    assert plan.result.output_watermark == 2
    assert plan.result.state == WikiProjectionState.conflicted


def test_user_claimed_reserved_path_matches_case_insensitively() -> None:
    claimed = _reserved("guides/Index.md", b"# User index\n", owned=False)

    plan = plan_wiki_projection(
        _request(),
        _snapshot(reserved_documents=(claimed,)),
    )

    assert plan.writes == ()
    assert plan.result.conflicts[0].path == "guides/index.md"
    assert plan.result.output_watermark == 2
    assert plan.result.state == WikiProjectionState.conflicted


def test_projector_only_advance_preserves_complete_projection_bytes() -> None:
    initial_snapshot = _snapshot()
    initial = plan_wiki_projection(
        _request(reason=WikiProjectionReason.manual_rebuild, scopes=()),
        initial_snapshot,
    )
    projector_change = WikiSourceChange(
        partition_position=4,
        operation=WikiChangeOperation.updated,
        path="index.md",
        title="Project 88",
        accepted_at=datetime(2026, 8, 29, 18, 31, tzinfo=timezone.utc),
        materialized=True,
        source="wiki_projector",
    )
    snapshot = replace(
        initial_snapshot,
        source_partition_position=4,
        current_output_watermark=3,
        source_accepted_at=projector_change.accepted_at,
        changes=(*initial_snapshot.changes, projector_change),
        reserved_documents=tuple(_reserved(write.path, write.content) for write in initial.writes),
    )

    plan = plan_wiki_projection(_request(position=4, scopes=()), snapshot)

    assert plan.writes == ()
    assert plan.unchanged_paths == tuple(write.path for write in initial.writes)
    assert plan.result.output_watermark == 4
    assert plan.result.state == WikiProjectionState.current


def test_projector_only_advance_repairs_missing_projection_document() -> None:
    initial_snapshot = _snapshot()
    initial = plan_wiki_projection(
        _request(reason=WikiProjectionReason.manual_rebuild, scopes=()),
        initial_snapshot,
    )
    projector_change = WikiSourceChange(
        partition_position=4,
        operation=WikiChangeOperation.updated,
        path="index.md",
        title="Project 88",
        accepted_at=ACCEPTED_AT,
        materialized=True,
        source="wiki_projector",
    )
    snapshot = replace(
        initial_snapshot,
        source_partition_position=4,
        current_output_watermark=3,
        changes=(*initial_snapshot.changes, projector_change),
        reserved_documents=tuple(
            _reserved(write.path, write.content)
            for write in initial.writes
            if write.path != "guides/deep/log.md"
        ),
    )

    plan = plan_wiki_projection(_request(position=4, scopes=()), snapshot)

    assert tuple(write.path for write in plan.writes) == ("guides/deep/log.md",)
    assert "Updated [[index|Project 88]]" not in plan.writes[0].content.decode()


def test_requested_scopes_cannot_omit_a_changed_note_scope() -> None:
    changed_note = WikiSourceNote(
        path="secret/note.md",
        title="Secret note",
        note_type="Note",
        checksum="secret-checksum",
    )
    changed = WikiSourceChange(
        partition_position=3,
        operation=WikiChangeOperation.updated,
        path=changed_note.path,
        title=changed_note.title,
        accepted_at=ACCEPTED_AT,
        materialized=True,
        source="web",
    )
    snapshot = replace(
        _snapshot(),
        notes=(*_snapshot().notes, changed_note),
        changes=(changed,),
    )

    plan = plan_wiki_projection(_request(scopes=("guides",)), snapshot)

    assert {write.path for write in plan.writes} >= {
        "secret/index.md",
        "secret/log.md",
    }
    assert plan.result.output_watermark == 3


def test_full_rebuild_covers_every_note_directory() -> None:
    request = _request(
        reason=WikiProjectionReason.import_rebuild,
        scopes=(),
    )

    plan = plan_wiki_projection(request, _snapshot())

    assert {write.path for write in plan.writes} == {
        "index.md",
        "log.md",
        "guides/index.md",
        "guides/log.md",
        "guides/deep/index.md",
        "guides/deep/log.md",
    }


def test_full_rebuild_covers_orphaned_reserved_document_scopes() -> None:
    request = _request(
        reason=WikiProjectionReason.manual_rebuild,
        scopes=(),
    )
    orphaned_index = _reserved("orphaned/index.md", b"# Stale index\n")
    orphaned_log = _reserved("orphaned/log.md", b"# Stale log\n")
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=3,
        current_output_watermark=2,
        source_accepted_at=ACCEPTED_AT,
        notes=(),
        changes=(
            WikiSourceChange(
                partition_position=3,
                operation=WikiChangeOperation.deleted,
                path="orphaned/last-note.md",
                title="Last note",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
        reserved_documents=(orphaned_index, orphaned_log),
    )

    plan = plan_wiki_projection(request, snapshot)

    assert {write.path for write in plan.writes} == {
        "index.md",
        "log.md",
        "orphaned/index.md",
        "orphaned/log.md",
    }
    rendered = {write.path: write.content.decode() for write in plan.writes}
    assert "No concepts have been projected" in rendered["orphaned/index.md"]
    assert "Deleted `orphaned/last-note.md`" in rendered["orphaned/log.md"]


def test_projection_rejects_a_snapshot_ahead_of_the_requested_watermark() -> None:
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=2,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=(),
        changes=(
            WikiSourceChange(
                partition_position=1,
                operation=WikiChangeOperation.deleted,
                path="included/note.md",
                title="Included",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
            WikiSourceChange(
                partition_position=2,
                operation=WikiChangeOperation.deleted,
                path="future/note.md",
                title="Future",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
    )

    with pytest.raises(ValueError, match="exact as-of source snapshot"):
        plan_wiki_projection(
            _request(
                position=1,
                reason=WikiProjectionReason.manual_rebuild,
                scopes=(),
            ),
            snapshot,
        )


def test_moved_change_requires_previous_path() -> None:
    with pytest.raises(ValueError, match="requires previous_path"):
        plan_wiki_projection(
            _request(),
            WikiProjectionSnapshot(
                project_id="project-88",
                project_name="Project 88",
                source_partition_position=3,
                current_output_watermark=2,
                source_accepted_at=ACCEPTED_AT,
                notes=(),
                changes=(
                    WikiSourceChange(
                        partition_position=3,
                        operation=WikiChangeOperation.moved,
                        path="guides/new.md",
                        title="Moved",
                        accepted_at=ACCEPTED_AT,
                        materialized=True,
                        source="web",
                    ),
                ),
            ),
        )


def test_created_and_moved_changes_render_in_the_log() -> None:
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=2,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=(),
        changes=(
            WikiSourceChange(
                partition_position=1,
                operation=WikiChangeOperation.created,
                path="created.md",
                title="Created",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
            WikiSourceChange(
                partition_position=2,
                operation=WikiChangeOperation.moved,
                path="moved.md",
                previous_path="old.md",
                title="Moved",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
    )

    plan = plan_wiki_projection(_request(position=2, scopes=()), snapshot)
    log = next(write.content.decode() for write in plan.writes if write.path == "log.md")

    assert "Created [[created|Created]]" in log
    assert "Moved `old.md` to [[moved|Moved]]" in log


def test_log_preserves_ampersands_in_code_formatted_paths() -> None:
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=2,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=(),
        changes=(
            WikiSourceChange(
                partition_position=1,
                operation=WikiChangeOperation.moved,
                path="new.md",
                previous_path="old&draft.md",
                title="Moved",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
            WikiSourceChange(
                partition_position=2,
                operation=WikiChangeOperation.deleted,
                path="retired&archived.md",
                title="Deleted",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
    )

    plan = plan_wiki_projection(_request(position=2, scopes=()), snapshot)
    log = next(write.content.decode() for write in plan.writes if write.path == "log.md")

    assert "Moved `old&draft.md` to [[new|Moved]]" in log
    assert "Deleted `retired&archived.md`" in log
    assert "&amp;" not in log


def test_absolute_paths_are_rejected_at_the_contract_boundary() -> None:
    with pytest.raises(ValueError, match="project-relative"):
        WikiSourceNote(
            path="/outside.md",
            title="Outside",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize("path", ("C:/outside.md", "C:\\outside.md"))
def test_windows_drive_paths_are_rejected_at_the_contract_boundary(path: str) -> None:
    with pytest.raises(ValueError, match="project-relative"):
        WikiSourceNote(
            path=path,
            title="Outside",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize("path", ("notes//foo.md", "notes/./foo.md", "note.md/"))
def test_noncanonical_paths_are_rejected_at_the_contract_boundary(path: str) -> None:
    with pytest.raises(ValueError, match="project-relative"):
        WikiSourceNote(
            path=path,
            title="Noncanonical",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize(
    "path",
    (
        "notes/closing].md",
        "notes/opening[.md",
        "notes/alias|target.md",
        "notes/code`span.md",
        "notes/html<tag.md",
        "notes/line\nbreak.md",
        "notes/cross::project.md",
    ),
)
def test_wikilink_delimiters_are_rejected_at_the_contract_boundary(path: str) -> None:
    with pytest.raises(ValueError, match="unsupported Markdown delimiters"):
        WikiSourceNote(
            path=path,
            title="Unsupported",
            note_type="Note",
            checksum="checksum",
        )


@pytest.mark.parametrize("path", ("", "note.txt"))
def test_non_markdown_note_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="project-relative Markdown"):
        WikiSourceNote(
            path=path,
            title="Unsupported",
            note_type="Note",
            checksum="checksum",
        )


def test_parent_segments_are_rejected_at_the_contract_boundary() -> None:
    with pytest.raises(ValueError, match="project-relative and normalized"):
        WikiSourceNote(
            path="notes/../outside.md",
            title="Outside",
            note_type="Note",
            checksum="checksum",
        )


def test_projection_order_is_deterministic_for_case_only_names() -> None:
    notes = (
        WikiSourceNote(
            path="foo.md",
            title="same",
            note_type="Note",
            checksum="lower",
        ),
        WikiSourceNote(
            path="Foo.md",
            title="Same",
            note_type="Note",
            checksum="upper",
        ),
    )
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=0,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=notes,
        changes=(),
    )
    reverse_snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=0,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=tuple(reversed(notes)),
        changes=(),
    )

    first = plan_wiki_projection(_request(position=0, scopes=()), snapshot)
    second = plan_wiki_projection(_request(position=0, scopes=()), reverse_snapshot)

    assert first.writes == second.writes


def test_projection_escapes_dynamic_markdown_structure() -> None:
    injected_title = "Bad]]\n- relates_to [[evil"
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name=f"Project\n{injected_title}",
        source_partition_position=1,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=(
            WikiSourceNote(
                path="safe-target.md",
                title=injected_title,
                note_type="Note",
                checksum="unsafe",
            ),
        ),
        changes=(
            WikiSourceChange(
                partition_position=1,
                operation=WikiChangeOperation.updated,
                path="safe-target.md",
                title=injected_title,
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
    )

    plan = plan_wiki_projection(_request(position=1, scopes=()), snapshot)
    rendered = {write.path: write.content.decode() for write in plan.writes}

    assert "\n- relates_to [[evil" not in rendered["index.md"]
    assert "\n- relates_to [[evil" not in rendered["log.md"]
    assert "[[safe-target|Bad&#93;&#93; - relates_to &#91;&#91;evil]]" in rendered["index.md"]


@pytest.mark.parametrize(
    ("title", "escaped_title"),
    (
        ("A &amp; B", "A &amp;amp; B"),
        ("A &#93; B", "A &amp;#93; B"),
    ),
)
def test_projection_preserves_literal_entity_looking_titles(
    title: str,
    escaped_title: str,
) -> None:
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        source_partition_position=1,
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=(
            WikiSourceNote(
                path="entity-title.md",
                title=title,
                note_type="Note",
                checksum="entity-title",
            ),
        ),
        changes=(
            WikiSourceChange(
                partition_position=1,
                operation=WikiChangeOperation.created,
                path="entity-title.md",
                title=title,
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="web",
            ),
        ),
    )

    plan = plan_wiki_projection(_request(position=1, scopes=()), snapshot)
    rendered = {write.path: write.content.decode() for write in plan.writes}

    assert f"[[entity-title|{escaped_title}]]" in rendered["index.md"]
