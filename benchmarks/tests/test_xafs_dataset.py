"""xAFS loader tests: discovery, subsetting, verbatim fields, fail-fast parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory_benchmarks.datasets.xafs import (
    XAFS_FAMILIES,
    load_xafs,
    load_xafs_persona,
)
from xafs_fixture import (
    DP1_FILES,
    DP1_INVOICE_AMOUNT,
    DP1_Q1_PROMPT,
    dp1_questions,
    question,
    write_persona,
    write_xafs_root,
)


def test_family_constant_matches_upstream_keys() -> None:
    assert XAFS_FAMILIES == ("single_hop", "multi_hop", "format_spanning")


# --- Discovery and persona selection ---


def test_load_discovers_every_persona_dir(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    # Non-persona entries must be ignored, not rejected.
    (root / "README.md").write_text("upstream readme", encoding="utf-8")
    (root / "misc").mkdir()

    personas = load_xafs(root)

    assert [persona.persona_id for persona in personas] == ["dp_001", "dp_002"]
    assert personas[0].root == root / "dp_001"


def test_persona_subset_selection_dedupes_and_sorts(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)

    personas = load_xafs(root, ["dp_002", "dp_002", "dp_001"])

    assert [persona.persona_id for persona in personas] == ["dp_001", "dp_002"]
    only_two = load_xafs(root, ["dp_002"])
    assert [persona.persona_id for persona in only_two] == ["dp_002"]


def test_unknown_persona_rejected_listing_available(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)

    with pytest.raises(ValueError, match=r"Unknown xAFS personas \['dp_009'\]") as excinfo:
        load_xafs(root, ["dp_001", "dp_009"])
    assert "dp_001" in str(excinfo.value)
    assert "dp_002" in str(excinfo.value)


def test_missing_root_names_the_fetch_script(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="download.sh"):
        load_xafs(tmp_path / "nope")


def test_root_without_persona_dirs_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "empty-root"
    (root / "not-a-persona").mkdir(parents=True)

    with pytest.raises(ValueError, match="No xAFS persona directories"):
        load_xafs(root)


# --- Verbatim fields, ordering, extras ---


def test_data_files_cover_every_extension_sorted(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)

    (persona,) = load_xafs(root, ["dp_001"])

    assert persona.data_files == tuple(sorted(DP1_FILES))
    assert "data/mail/2026-04-01_invoice.eml" in persona.data_files
    assert all(relpath.startswith("data/") for relpath in persona.data_files)


def test_question_fields_round_trip_verbatim(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)

    (persona,) = load_xafs(root, ["dp_001"])

    assert [q.id for q in persona.questions] == ["q01", "q02", "q03", "q04"]
    first = persona.questions[0]
    assert first.persona_id == "dp_001"
    assert first.family == "single_hop"
    assert first.prompt == DP1_Q1_PROMPT
    assert first.gold_file_ids == ("data/client/kickoff-transcript.md",)
    assert first.gold_answer == DP1_INVOICE_AMOUNT
    # Unknown upstream keys survive verbatim; known keys never leak into extras.
    assert dict(first.extras) == {"difficulty": "easy"}
    assert dict(persona.questions[1].extras) == {}


def test_question_order_is_upstream_array_order_not_sorted(tmp_path: Path) -> None:
    files = {"data/a.md": "alpha\n", "data/b.md": "beta\n"}
    questions = [
        question("q02", "single_hop", "Second first?", ["data/b.md"], "beta"),
        question("q01", "multi_hop", "First second?", ["data/a.md", "data/b.md"], "alpha"),
    ]
    persona_dir = write_persona(tmp_path, "dp_007", files, questions)

    persona = load_xafs_persona(persona_dir)

    assert [q.id for q in persona.questions] == ["q02", "q01"]


# --- Fail-fast parsing (stated assumptions -> loud errors, never guesses) ---


def _dp1_dir(tmp_path: Path) -> Path:
    return write_xafs_root(tmp_path) / "dp_001"


def _rewrite_questions(persona_dir: Path, payload: object) -> None:
    (persona_dir / "question.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_question_json_fails(tmp_path: Path) -> None:
    persona_dir = _dp1_dir(tmp_path)
    (persona_dir / "question.json").unlink()

    with pytest.raises(FileNotFoundError, match="no question.json"):
        load_xafs_persona(persona_dir)


def test_missing_data_dir_fails(tmp_path: Path) -> None:
    persona_dir = tmp_path / "dp_003"
    persona_dir.mkdir()
    _rewrite_questions(persona_dir, dp1_questions())

    with pytest.raises(FileNotFoundError, match="no data/ directory"):
        load_xafs_persona(persona_dir)


def test_empty_data_dir_fails(tmp_path: Path) -> None:
    persona_dir = tmp_path / "dp_003"
    (persona_dir / "data").mkdir(parents=True)
    _rewrite_questions(persona_dir, dp1_questions())

    with pytest.raises(ValueError, match="data/ directory is empty"):
        load_xafs_persona(persona_dir)


@pytest.mark.parametrize("payload", [{"questions": []}, [], "not a list"])
def test_question_json_must_be_nonempty_array(tmp_path: Path, payload: object) -> None:
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, payload)

    with pytest.raises(ValueError, match="non-empty JSON array"):
        load_xafs_persona(persona_dir)


def test_non_object_question_record_fails(tmp_path: Path) -> None:
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, ["not an object"])

    with pytest.raises(ValueError, match="is not an object"):
        load_xafs_persona(persona_dir)


def test_missing_required_key_is_named(tmp_path: Path) -> None:
    broken = dp1_questions()
    del broken[1]["family"]
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match=r"missing keys \['family'\]"):
        load_xafs_persona(persona_dir)


@pytest.mark.parametrize("key", ["id", "prompt", "gold_answer"])
@pytest.mark.parametrize("bad_value", ["", "   ", 3])
def test_empty_or_non_string_required_field_fails(
    tmp_path: Path, key: str, bad_value: object
) -> None:
    broken = dp1_questions()
    broken[0][key] = bad_value
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match=f"empty or non-string {key!r}"):
        load_xafs_persona(persona_dir)


def test_unknown_family_fails_listing_the_three(tmp_path: Path) -> None:
    broken = dp1_questions()
    broken[0]["family"] = "temporal"
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="unknown family 'temporal'") as excinfo:
        load_xafs_persona(persona_dir)
    assert "single_hop" in str(excinfo.value)


@pytest.mark.parametrize("bad_gold", [[], "data/client/kickoff-transcript.md"])
def test_empty_or_non_list_gold_file_ids_fails(tmp_path: Path, bad_gold: object) -> None:
    broken = dp1_questions()
    broken[0]["gold_file_ids"] = bad_gold
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="empty or non-list gold_file_ids"):
        load_xafs_persona(persona_dir)


@pytest.mark.parametrize("bad_item", [3, "", "   "])
def test_non_string_gold_item_fails(tmp_path: Path, bad_item: object) -> None:
    broken = dp1_questions()
    broken[0]["gold_file_ids"] = [bad_item]
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="must be non-empty strings"):
        load_xafs_persona(persona_dir)


@pytest.mark.parametrize("escaping", ["/etc/passwd", "data/../question.json"])
def test_escaping_gold_path_fails(tmp_path: Path, escaping: str) -> None:
    broken = dp1_questions()
    broken[0]["gold_file_ids"] = [escaping]
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="escapes the persona"):
        load_xafs_persona(persona_dir)


def test_gold_path_outside_data_fails(tmp_path: Path) -> None:
    broken = dp1_questions()
    broken[0]["gold_file_ids"] = ["question.json"]
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="not data/-prefixed"):
        load_xafs_persona(persona_dir)


def test_dangling_gold_reference_names_the_fetch_script(tmp_path: Path) -> None:
    # A gold file missing on disk means a truncated download, not a soft skip.
    broken = dp1_questions()
    broken[0]["gold_file_ids"] = ["data/client/deleted.md"]
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, broken)

    with pytest.raises(ValueError, match="gold file missing on disk") as excinfo:
        load_xafs_persona(persona_dir)
    assert "download.sh" in str(excinfo.value)


def test_duplicate_question_id_fails(tmp_path: Path) -> None:
    duplicated: list[dict[str, Any]] = dp1_questions()
    duplicated[1]["id"] = "q01"
    persona_dir = _dp1_dir(tmp_path)
    _rewrite_questions(persona_dir, duplicated)

    with pytest.raises(ValueError, match="duplicate question id 'q01'"):
        load_xafs_persona(persona_dir)
