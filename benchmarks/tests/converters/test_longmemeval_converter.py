"""Tests for the LongMemEval-S grouped corpus converter."""

from __future__ import annotations

import json

import pytest

from basic_memory_benchmarks.converters.longmemeval_to_corpus import (
    convert_longmemeval_to_corpus,
)


def _entry(
    question_id: str = "q1",
    question_type: str = "single-session-user",
    answer_session_ids: list[str] | None = None,
    session_ids: list[str] | None = None,
) -> dict:
    session_ids = session_ids or ["filler_001", "answer_abc", "filler_002"]
    return {
        "question_id": question_id,
        "question_type": question_type,
        "question": "What degree did I graduate with?",
        "answer": "Business Administration",
        "question_date": "2023/05/30 (Tue) 23:40",
        "haystack_session_ids": session_ids,
        "haystack_dates": [f"2023/05/{i + 1:02d} (Mon) 10:00" for i in range(len(session_ids))],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": f"hello in session number {index}",
                    "has_answer": sid.startswith("answer_"),
                },
                {"role": "assistant", "content": "hi there"},
            ]
            for index, sid in enumerate(session_ids)
        ],
        "answer_session_ids": answer_session_ids or ["answer_abc"],
    }


def _write_dataset(tmp_path, entries):
    path = tmp_path / "longmemeval_s.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


class TestConvertLongMemEval:
    def test_grouped_layout_and_queries(self, tmp_path):
        dataset = _write_dataset(tmp_path, [_entry("q1"), _entry("q2")])
        out = tmp_path / "out"

        groups_dir, queries_path, doc_count, query_count = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=out
        )

        assert doc_count == 6
        assert query_count == 2
        assert (groups_dir / "q1" / "docs").is_dir()
        assert (groups_dir / "q2" / "docs").is_dir()

        queries = json.loads(queries_path.read_text())
        by_id = {q["id"]: q for q in queries}
        assert by_id["q1"]["group"] == "q1"
        assert by_id["q1"]["category"] == "single-session-user"
        assert by_id["q1"]["expected_answer"] == "Business Administration"
        assert by_id["q1"]["metadata"]["question_date"] == "2023/05/30 (Tue) 23:40"
        assert by_id["q1"]["metadata"]["abstention"] is False

    def test_ground_truth_leakage_is_scrubbed(self, tmp_path):
        """Evidence markers in the raw data must not survive conversion."""
        dataset = _write_dataset(tmp_path, [_entry("q1")])
        out = tmp_path / "out"

        groups_dir, queries_path, _, _ = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=out
        )

        doc_paths = sorted((groups_dir / "q1" / "docs").glob("*.md"))
        assert [p.stem for p in doc_paths] == ["q1-s000", "q1-s001", "q1-s002"]
        corpus_text = "".join(p.read_text() for p in doc_paths)
        assert "answer_" not in corpus_text
        assert "has_answer" not in corpus_text

        queries = json.loads(queries_path.read_text())
        # Ground truth maps through the neutral ids: answer_abc was index 1.
        assert queries[0]["ground_truth"] == ["q1-s001"]

    def test_duplicate_sessions_kept_once(self, tmp_path):
        entry = _entry(
            "q1",
            session_ids=["filler_001", "answer_abc", "filler_001"],
        )
        dataset = _write_dataset(tmp_path, [entry])

        _, _, doc_count, _ = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        assert doc_count == 2

    def test_abstention_flag_from_question_id(self, tmp_path):
        dataset = _write_dataset(tmp_path, [_entry("q9_abs")])
        _, queries_path, _, _ = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        queries = json.loads(queries_path.read_text())
        assert queries[0]["metadata"]["abstention"] is True

    def test_session_date_in_doc(self, tmp_path):
        dataset = _write_dataset(tmp_path, [_entry("q1")])
        groups_dir, _, _, _ = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        doc = (groups_dir / "q1" / "docs" / "q1-s000.md").read_text()
        assert "session_date: 2023/05/01 (Mon) 10:00" in doc
        assert "# Chat session on 2023/05/01 (Mon) 10:00" in doc
        assert "- **User:** hello in session number 0" in doc

    def test_missing_answer_session_raises(self, tmp_path):
        entry = _entry("q1", answer_session_ids=["not_in_haystack"])
        dataset = _write_dataset(tmp_path, [entry])
        with pytest.raises(ValueError, match="not present in haystack"):
            convert_longmemeval_to_corpus(dataset_path=dataset, output_dir=tmp_path / "out")

    def test_max_questions(self, tmp_path):
        dataset = _write_dataset(tmp_path, [_entry("q1"), _entry("q2"), _entry("q3")])
        _, _, _, query_count = convert_longmemeval_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out", max_questions=2
        )
        assert query_count == 2


class TestStratifiedSlice:
    def test_stratified_covers_types_evenly(self, tmp_path):
        entries = []
        for t in ["single-session-user", "multi-session", "temporal-reasoning"]:
            for i in range(10):
                entries.append(_entry(f"{t[:4]}{i}", question_type=t))
        dataset = _write_dataset(tmp_path, entries)

        _, queries_path, _, count = convert_longmemeval_to_corpus(
            dataset_path=dataset,
            output_dir=tmp_path / "out",
            max_questions=9,
            stratified=True,
        )

        import collections

        queries = json.loads(queries_path.read_text())
        by_type = collections.Counter(q["category"] for q in queries)
        assert count == 9
        assert all(v == 3 for v in by_type.values())
        sampling = json.loads((tmp_path / "out" / "sampling.json").read_text())
        assert sampling["seed"] == 42

    def test_stratified_deterministic(self, tmp_path):
        entries = [_entry(f"q{i}", question_type="multi-session") for i in range(20)]
        dataset = _write_dataset(tmp_path, entries)
        ids = []
        for run in range(2):
            _, qp, _, _ = convert_longmemeval_to_corpus(
                dataset_path=dataset,
                output_dir=tmp_path / f"out{run}",
                max_questions=5,
                stratified=True,
            )
            ids.append([q["id"] for q in json.loads(qp.read_text())])
        assert ids[0] == ids[1]

    def test_prefix_mode_unchanged(self, tmp_path):
        entries = [_entry(f"q{i}") for i in range(5)]
        dataset = _write_dataset(tmp_path, entries)
        _, qp, _, count = convert_longmemeval_to_corpus(
            dataset_path=dataset,
            output_dir=tmp_path / "out",
            max_questions=2,
        )
        assert count == 2
        assert [q["id"] for q in json.loads(qp.read_text())] == ["q0", "q1"]
