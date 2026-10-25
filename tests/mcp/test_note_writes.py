"""NoteChange: the result every bm_* write reports."""

from datetime import datetime, timezone

import pytest

from basic_memory.mcp.note_targets import PathQualifier
from basic_memory.mcp.note_writes import NoteChange
from basic_memory.schemas.v2.entity import EntityResponseV2

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def _note(file_path: str = "Plans/q4.md") -> EntityResponseV2:
    return EntityResponseV2(
        external_id="abc",
        id=1,
        title="Q4",
        note_type="note",
        permalink="plans/q4",
        file_path=file_path,
        db_checksum="sum",
        created_at=NOW,
        updated_at=NOW,
    )


def test_paths_are_qualified_like_the_request():
    change = NoteChange.of(
        _note(),
        action="moved",
        paths=PathQualifier(prefix="research"),
        requested_file_path="plans/q4.md",
        previous_file_path="notes/q4.md",
    )

    assert change.file_path == "research/Plans/q4.md"
    assert change.requested_file_path == "research/plans/q4.md"
    assert change.previous_file_path == "research/notes/q4.md"
    assert change.text_lines()[:2] == [
        "Moved research/notes/q4.md to research/Plans/q4.md",
        "requested: research/plans/q4.md",
    ]


def test_a_requested_path_equal_to_the_written_one_is_dropped():
    change = NoteChange.of(
        _note(),
        action="created",
        paths=PathQualifier(prefix=None),
        requested_file_path="Plans/q4.md",
    )

    assert change.requested_file_path is None
    assert "requested_file_path" not in change.as_json()
    assert "previous_file_path" not in change.as_json()
    assert change.as_json()["checksum"] == "sum"


def test_text_headline_carries_the_detail():
    change = NoteChange.of(_note(), action="edited", paths=PathQualifier(prefix=None))

    assert change.text_lines(detail="append")[0] == "Edited Plans/q4.md (append)"
    assert change.text_lines()[-1] == "checksum: sum"


@pytest.mark.parametrize("action", ["created", "updated", "edited", "moved"])
def test_accepted_change_does_not_report_unavailable_graph_counts(action):
    note = _note().model_copy(
        update={
            "content": "- [goal] ship\n- relates_to [[Missing]]",
            "observations": [],
            "relations": [],
        }
    )
    change = NoteChange.of(
        note, action=action, paths=PathQualifier(prefix=None), previous_file_path="notes/q4.md"
    )

    assert {"observations", "relations", "unresolved_relations"}.isdisjoint(change.as_json())
    assert "observations:" not in "\n".join(change.text_lines())
    assert "relations:" not in "\n".join(change.text_lines())
