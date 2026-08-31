"""xAFS converter tests: verbatim copy, anti-leakage, manifest, corrections, sampling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory_benchmarks.converters.xafs_to_corpus import (
    _copy_persona_files,
    _stratified_allocation,
    build_judge_rubric,
    convert_xafs_to_corpus,
    load_xafs_corrections,
    sample_xafs_audit,
    xafs_group_id,
)
from basic_memory_benchmarks.datasets.xafs import XAFS_REVISION, XafsPersona
from basic_memory_benchmarks.utils import sha256_file
from xafs_fixture import (
    DP1_FILES,
    DP1_Q1_PROMPT,
    DP1_Q3_PROMPT,
    question,
    write_persona,
    write_xafs_root,
)

EXPECTED_TASK_IDS = [
    "xafs-dp001-q01",
    "xafs-dp001-q02",
    "xafs-dp001-q03",
    "xafs-dp001-q04",
    "xafs-dp002-q01",
    "xafs-dp002-q02",
]


def _convert(tmp_path: Path, **kwargs: Any) -> tuple[Path, Path, int, int]:
    root = write_xafs_root(tmp_path)
    return convert_xafs_to_corpus(dataset_root=root, output_dir=tmp_path / "generated", **kwargs)


def _tasks(output_dir: Path) -> list[dict[str, Any]]:
    return json.loads((output_dir / "tasks.json").read_text(encoding="utf-8"))


def _conversion(output_dir: Path) -> dict[str, Any]:
    return json.loads((output_dir / "conversion.json").read_text(encoding="utf-8"))


def test_group_id_strips_the_underscore() -> None:
    assert xafs_group_id("dp_001") == "xafs-dp001"


def test_verbatim_copy_including_non_markdown(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    groups_dir, tasks_path, file_count, task_count = _convert(tmp_path)

    assert groups_dir == tmp_path / "generated" / "groups"
    assert tasks_path == tmp_path / "generated" / "tasks.json"
    assert file_count == 6
    assert task_count == 6
    # Byte-for-byte, data/ relpaths preserved (one path vocabulary with
    # gold_file_ids), no frontmatter render — the .eml included.
    for relpath in DP1_FILES:
        source = (root / "dp_001" / relpath).read_bytes()
        copied = (groups_dir / "xafs-dp001" / "docs" / relpath).read_bytes()
        assert copied == source, relpath
    assert (groups_dir / "xafs-dp001" / "docs" / "data/mail/2026-04-01_invoice.eml").is_file()


def test_answer_key_never_lands_in_docs(tmp_path: Path) -> None:
    groups_dir, _, _, _ = _convert(tmp_path)

    leaked = [path for path in groups_dir.rglob("question.json")]
    assert leaked == []
    # Only data/ subtrees are copied: nothing outside docs/data exists.
    for group_dir in groups_dir.iterdir():
        docs = group_dir / "docs"
        assert sorted(entry.name for entry in docs.iterdir()) == ["data"]


@pytest.mark.parametrize("forbidden", ["question.json", "SCENARIO.md", "facts.json"])
def test_forbidden_basename_inside_data_aborts(tmp_path: Path, forbidden: str) -> None:
    # An answer-key/scenario file inside data/ means the upstream layout
    # changed under us; conversion must abort, not ingest it.
    root = tmp_path / "root"
    write_persona(
        root,
        "dp_001",
        {"data/notes/a.md": "alpha\n", f"data/misc/{forbidden}": "leak\n"},
        [question("q01", "single_hop", "Alpha?", ["data/notes/a.md"], "alpha")],
    )

    with pytest.raises(ValueError, match="answer-key/scenario file"):
        convert_xafs_to_corpus(dataset_root=root, output_dir=tmp_path / "out")


def test_escaping_relpath_defense_in_depth(tmp_path: Path) -> None:
    # data_files come from rglob so this is unreachable via load_xafs; the
    # copy still refuses a crafted/symlinked layout rather than escaping.
    persona = XafsPersona(
        persona_id="dp_666",
        root=tmp_path,
        data_files=("data/../evil.md",),
        questions=(),
    )
    with pytest.raises(ValueError, match="escapes the persona"):
        _copy_persona_files(persona, tmp_path / "group")


def _symlink_or_skip(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links not supported on this filesystem")


def test_symlinked_data_file_aborts_conversion(tmp_path: Path) -> None:
    # A symlink named like corpus data passes the relpath-string checks under
    # its own name; copying it would materialize the answer key into the
    # ingested haystack, so conversion must refuse it.
    root = tmp_path / "root"
    write_persona(
        root,
        "dp_001",
        {"data/notes/a.md": "alpha\n"},
        [question("q01", "single_hop", "Alpha?", ["data/notes/a.md"], "alpha")],
    )
    _symlink_or_skip(root / "dp_001" / "data" / "leak.md", "../question.json")

    with pytest.raises(ValueError, match="symlink"):
        convert_xafs_to_corpus(dataset_root=root, output_dir=tmp_path / "out")


def test_conversion_manifest_counts_and_empty_skipped(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    _convert(tmp_path)

    manifest = _conversion(tmp_path / "generated")
    assert manifest["dataset_id"] == "xafs"
    assert manifest["revision"] == XAFS_REVISION
    assert "secondary dataset" in manifest["license_note"]
    assert manifest["file_count"] == 6
    assert manifest["task_count"] == 6
    # The never-silently-dropped contract: the list exists and is empty.
    assert manifest["skipped"] == []
    assert manifest["corrections"] == {"applied": 0, "excluded": []}

    dp1, dp2 = manifest["personas"]
    assert dp1["persona_id"] == "dp_001"
    assert dp1["group_id"] == "xafs-dp001"
    assert dp1["file_count"] == 4
    assert dp1["files_by_extension"] == {".eml": 1, ".md": 3}
    assert dp1["question_count"] == 4
    assert dp1["question_file_sha256"] == sha256_file(root / "dp_001" / "question.json")
    assert dp2["files_by_extension"] == {".md": 2}


def test_tasks_manifest_rows_route_families_to_judge_graded_tasks(tmp_path: Path) -> None:
    _convert(tmp_path)

    tasks = _tasks(tmp_path / "generated")
    assert [row["id"] for row in tasks] == EXPECTED_TASK_IDS
    # skill = family: the per-skill report becomes the per-question-type
    # breakdown; every question routes to the agent harness as judge-graded.
    assert [row["skill"] for row in tasks] == [
        "single_hop",
        "multi_hop",
        "format_spanning",
        "format_spanning",
        "single_hop",
        "multi_hop",
    ]
    first = tasks[0]
    assert first["group"] == "xafs-dp001"
    assert first["source"] == f"supermemory/xAFS dp_001 q01 @{XAFS_REVISION[:8]}"
    assert first["prompt"] == DP1_Q1_PROMPT
    (grader,) = first["graders"]
    assert grader["kind"] == "judge_rubric"
    assert grader["rubric"] == build_judge_rubric(DP1_Q1_PROMPT, "$2,034")
    assert f"Question: {DP1_Q1_PROMPT}" in grader["rubric"]
    assert "Gold answer: $2,034" in grader["rubric"]
    assert first["metadata"] == {
        "gold_file_ids": ["data/client/kickoff-transcript.md"],
        "gold_answer": "$2,034",
    }


def test_no_retrieval_queries_artifact_is_emitted(tmp_path: Path) -> None:
    # The retrieval diagnostic is deferred (provider doc-id collision); an
    # artifact no stage can consume correctly must not exist.
    _convert(tmp_path)

    assert not (tmp_path / "generated" / "queries.json").exists()


def test_persona_subsetting(tmp_path: Path) -> None:
    groups_dir, _, file_count, task_count = _convert(tmp_path, personas=["dp_001"])

    assert file_count == 4
    assert task_count == 4
    assert sorted(entry.name for entry in groups_dir.iterdir()) == ["xafs-dp001"]
    manifest = _conversion(tmp_path / "generated")
    assert manifest["converter"]["personas"] == ["dp_001"]
    assert [row["group"] for row in _tasks(tmp_path / "generated")] == ["xafs-dp001"] * 4


def test_checksums_deterministic_across_runs(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    convert_xafs_to_corpus(dataset_root=root, output_dir=tmp_path / "run-a")
    convert_xafs_to_corpus(dataset_root=root, output_dir=tmp_path / "run-b")

    manifest_a = json.loads((tmp_path / "run-a" / "conversion.json").read_text())
    manifest_b = json.loads((tmp_path / "run-b" / "conversion.json").read_text())
    assert manifest_a["personas"] == manifest_b["personas"]
    assert manifest_a["personas"][0]["data_files_sha256"]


def test_reconversion_prunes_files_gone_upstream(tmp_path: Path) -> None:
    # A revision bump re-fetched in place: `hf download` never prunes removed
    # files, and neither would overwrite-only copying — the stale file would
    # sit in the ingested haystack while conversion.json counts the new set.
    root = write_xafs_root(tmp_path)
    stale_upstream = root / "dp_001" / "data" / "notes" / "scratch.md"
    stale_upstream.write_text("draft that the next revision removes\n", encoding="utf-8")
    output_dir = tmp_path / "generated"
    convert_xafs_to_corpus(dataset_root=root, output_dir=output_dir)
    stale_copy = output_dir / "groups" / "xafs-dp001" / "docs" / "data" / "notes" / "scratch.md"
    assert stale_copy.is_file()

    stale_upstream.unlink()
    _, _, file_count, _ = convert_xafs_to_corpus(dataset_root=root, output_dir=output_dir)

    assert not stale_copy.exists()
    assert file_count == 6  # docs/ mirrors exactly this conversion's inputs


# --- Corrections hook (locomo-audit precedent) ---


def _write_corrections(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_correction_overrides_gold_answer_everywhere(tmp_path: Path) -> None:
    corrections = _write_corrections(
        tmp_path, {"dp_001/q01": {"prompt": DP1_Q1_PROMPT, "gold_answer": "$9,999"}}
    )
    _convert(tmp_path, corrections_path=corrections)

    tasks = _tasks(tmp_path / "generated")
    first = tasks[0]
    assert first["metadata"]["gold_answer"] == "$9,999"
    assert "Gold answer: $9,999" in first["graders"][0]["rubric"]
    manifest = _conversion(tmp_path / "generated")
    assert manifest["corrections"] == {"applied": 1, "excluded": []}
    assert manifest["converter"]["corrections_path"] == str(corrections)


def test_correction_prompt_drift_fails_loudly(tmp_path: Path) -> None:
    corrections = _write_corrections(
        tmp_path, {"dp_001/q01": {"prompt": "A different question?", "gold_answer": "$9,999"}}
    )
    with pytest.raises(ValueError, match="drifted"):
        _convert(tmp_path, corrections_path=corrections)


def test_correction_exclusion_drops_the_task_and_counts_it(tmp_path: Path) -> None:
    corrections = _write_corrections(
        tmp_path,
        {"dp_001/q03": {"prompt": DP1_Q3_PROMPT, "excluded": True, "reason": "ambiguous gold"}},
    )
    _, _, _, task_count = _convert(tmp_path, corrections_path=corrections)

    assert task_count == 5
    task_ids = [row["id"] for row in _tasks(tmp_path / "generated")]
    assert "xafs-dp001-q03" not in task_ids
    manifest = _conversion(tmp_path / "generated")
    assert manifest["corrections"]["excluded"] == [
        {"question": "dp_001/q03", "reason": "ambiguous gold"}
    ]


def test_stale_correction_for_selected_persona_fails(tmp_path: Path) -> None:
    corrections = _write_corrections(
        tmp_path, {"dp_001/q99": {"prompt": "Gone?", "gold_answer": "x"}}
    )
    with pytest.raises(ValueError, match=r"unknown questions: \['dp_001/q99'\]"):
        _convert(tmp_path, corrections_path=corrections)


def test_correction_for_unselected_persona_is_ignored_under_subsetting(tmp_path: Path) -> None:
    corrections = _write_corrections(
        tmp_path, {"dp_002/q01": {"prompt": "whatever", "gold_answer": "x"}}
    )
    _, _, _, task_count = _convert(tmp_path, personas=["dp_001"], corrections_path=corrections)

    assert task_count == 4


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not", "an", "object"], "must be a JSON object"),
        ({"dp_001/q01": "not an object"}, "is not an object"),
        ({"dp_001/q01": {"gold_answer": "x"}}, "needs the upstream 'prompt'"),
        ({"dp_001/q01": {"prompt": "p", "excluded": True}}, "excludes without a 'reason'"),
        (
            {"dp_001/q01": {"prompt": "p", "excluded": True, "reason": "r", "gold_answer": "x"}},
            "both excludes and overrides",
        ),
        ({"dp_001/q01": {"prompt": "p"}}, "must override 'gold_answer' or set 'excluded'"),
    ],
)
def test_malformed_corrections_fail(tmp_path: Path, payload: object, match: str) -> None:
    corrections = _write_corrections(tmp_path, payload)

    with pytest.raises(ValueError, match=match):
        load_xafs_corrections(corrections)


# --- Audit sampling ---


def test_stratified_allocation_uses_largest_remainder() -> None:
    # The real shipped counts: 20 * (33, 51, 26) / 110 -> floors (6, 9, 4)
    # with one remainder seat going to format_spanning (largest fraction).
    counts = {"single_hop": 33, "multi_hop": 51, "format_spanning": 26}
    assert _stratified_allocation(counts, 20) == {
        "single_hop": 6,
        "multi_hop": 9,
        "format_spanning": 5,
    }
    # Sample >= population: everything is selected.
    assert _stratified_allocation(counts, 200) == counts


def test_audit_sample_covers_all_when_n_exceeds_total(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    sample_path, sampled = sample_xafs_audit(
        dataset_root=root, output_dir=tmp_path / "audit", sample_size=20, seed=42
    )

    assert sampled == 6
    records = json.loads(sample_path.read_text(encoding="utf-8"))
    assert [(row["persona_id"], row["id"]) for row in records] == [
        ("dp_001", "q01"),
        ("dp_001", "q02"),
        ("dp_001", "q03"),
        ("dp_001", "q04"),
        ("dp_002", "q01"),
        ("dp_002", "q02"),
    ]
    q03 = records[2]
    assert q03["family"] == "format_spanning"
    assert q03["gold_file_ids"] == ["data/mail/2026-04-01_invoice.eml"]

    # Verbatim gold-file copies for the human reviewer, .eml included.
    copied = tmp_path / "audit" / "sources" / "dp_001-q03" / "data/mail/2026-04-01_invoice.eml"
    assert (
        copied.read_bytes() == (root / "dp_001" / "data/mail/2026-04-01_invoice.eml").read_bytes()
    )

    sample_md = (tmp_path / "audit" / "sample.md").read_text(encoding="utf-8")
    assert "## dp_001/q03 (format_spanning)" in sample_md
    assert DP1_Q3_PROMPT in sample_md
    assert "corrections.json" in sample_md


def test_audit_sample_is_stratified_and_seed_deterministic(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    sample_path_a, sampled = sample_xafs_audit(
        dataset_root=root, output_dir=tmp_path / "audit-a", sample_size=3, seed=7
    )
    sample_path_b, _ = sample_xafs_audit(
        dataset_root=root, output_dir=tmp_path / "audit-b", sample_size=3, seed=7
    )

    assert sampled == 3
    records = json.loads(sample_path_a.read_text(encoding="utf-8"))
    # 2/2/2 questions per family and n=3 -> exactly one per family.
    assert sorted(row["family"] for row in records) == [
        "format_spanning",
        "multi_hop",
        "single_hop",
    ]
    assert sample_path_a.read_text() == sample_path_b.read_text()


def test_resampling_prunes_stale_sources(tmp_path: Path) -> None:
    # Re-sampling into the same output_dir with a different --n/--seed must
    # not leave sources/ dirs the new audit-sample.json no longer references.
    root = write_xafs_root(tmp_path)
    audit_dir = tmp_path / "audit"
    sample_xafs_audit(dataset_root=root, output_dir=audit_dir, sample_size=20, seed=42)
    assert len(list((audit_dir / "sources").iterdir())) == 6

    sample_path, sampled = sample_xafs_audit(
        dataset_root=root, output_dir=audit_dir, sample_size=3, seed=7
    )

    assert sampled == 3
    records = json.loads(sample_path.read_text(encoding="utf-8"))
    expected_dirs = sorted(f"{row['persona_id']}-{row['id']}" for row in records)
    assert sorted(entry.name for entry in (audit_dir / "sources").iterdir()) == expected_dirs


def test_symlinked_gold_file_aborts_audit_sampling(tmp_path: Path) -> None:
    # Same hole as the persona copy: the loader's is_file() existence check
    # follows links, so a gold-file symlink would land its target in the
    # audit output. The sampler must refuse it.
    root = tmp_path / "root"
    write_persona(
        root,
        "dp_001",
        {"data/notes/a.md": "alpha\n"},
        [question("q01", "single_hop", "Alpha?", ["data/leak.md"], "alpha")],
    )
    _symlink_or_skip(root / "dp_001" / "data" / "leak.md", "../question.json")

    with pytest.raises(ValueError, match="symlink"):
        sample_xafs_audit(dataset_root=root, output_dir=tmp_path / "audit")
