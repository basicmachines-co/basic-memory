"""Deterministic Wiki Projector contract and byte-output tests."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from basic_memory.indexing.wiki_projector import (
    WikiChangeOperation,
    WikiProjectionReason,
    WikiProjectionRequest,
    WikiProjectionSnapshot,
    WikiProjectionState,
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
        projector_version="wiki/1.0.0",
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


def test_projector_generated_change_is_suppressed_from_writes_and_log() -> None:
    existing = _reserved("index.md", b"existing\n")
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        current_output_watermark=3,
        source_accepted_at=ACCEPTED_AT,
        notes=(),
        changes=(
            WikiSourceChange(
                partition_position=4,
                operation=WikiChangeOperation.updated,
                path="index.md",
                title="Project 88",
                accepted_at=ACCEPTED_AT,
                materialized=True,
                source="wiki_projector",
            ),
        ),
        reserved_documents=(existing,),
    )

    plan = plan_wiki_projection(_request(position=4, scopes=()), snapshot)

    assert plan.writes == ()
    assert plan.result.output_watermark == 4
    assert plan.result.state == WikiProjectionState.current


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


def test_moved_change_requires_previous_path() -> None:
    with pytest.raises(ValueError, match="requires previous_path"):
        plan_wiki_projection(
            _request(),
            WikiProjectionSnapshot(
                project_id="project-88",
                project_name="Project 88",
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


@pytest.mark.parametrize(
    "path",
    (
        "notes/closing].md",
        "notes/opening[.md",
        "notes/alias|target.md",
        "notes/code`span.md",
        "notes/html<tag.md",
        "notes/line\nbreak.md",
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


def test_projection_order_is_deterministic_for_case_only_names() -> None:
    notes = (
        WikiSourceNote(path="foo.md", title="same", note_type="Note", checksum="lower"),
        WikiSourceNote(path="Foo.md", title="Same", note_type="Note", checksum="upper"),
    )
    snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
        current_output_watermark=0,
        source_accepted_at=ACCEPTED_AT,
        notes=notes,
        changes=(),
    )
    reverse_snapshot = WikiProjectionSnapshot(
        project_id="project-88",
        project_name="Project 88",
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
