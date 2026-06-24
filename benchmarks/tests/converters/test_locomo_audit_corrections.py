"""Tests for applying Penfield audit corrections during LoCoMo conversion."""

from __future__ import annotations

import json

import pytest

from basic_memory_benchmarks.converters.locomo_to_corpus import convert_locomo_to_corpus
from basic_memory_benchmarks.datasets.locomo_audit import load_locomo_corrections


def _locomo_blob() -> list[dict]:
    return [
        {
            "conversation": {
                "session_1": [
                    {"speaker": "Melanie", "text": "I painted a sunrise last year."},
                ],
                "session_2": [
                    {"speaker": "Caroline", "text": "I studied counseling."},
                ],
            },
            "qa": [
                {
                    "question": "When did Melanie paint a sunrise?",
                    "answer": "2022",
                    "category": 2,
                    "evidence": ["D1:12"],
                },
                {
                    "question": "What did Caroline study?",
                    "answer": "Psychology, counseling certification",
                    "category": 1,
                    "evidence": ["D2:3"],
                },
            ],
        }
    ]


def _corrections() -> list[dict]:
    return [
        {
            "question_id": "locomo_0_qa1",
            "question": "What did Caroline study?",
            "golden_answer": "Psychology, counseling certification",
            "category": 1,
            "error_type": "HALLUCINATION",
            "cited_evidence": ["D2:3"],
            "correct_evidence": ["D1:1"],
            "correct_answer": "Counseling or mental health",
        }
    ]


@pytest.fixture
def converted(tmp_path):
    dataset = tmp_path / "locomo10.json"
    dataset.write_text(json.dumps(_locomo_blob()), encoding="utf-8")
    corrections = tmp_path / "corrections.json"
    corrections.write_text(json.dumps(_corrections()), encoding="utf-8")

    _, queries_path, _, _ = convert_locomo_to_corpus(
        dataset_path=dataset,
        output_dir=tmp_path / "out",
        audit_corrections_path=corrections,
    )
    return json.loads(queries_path.read_text())


class TestAuditCorrections:
    def test_corrected_answer_and_evidence_applied(self, converted):
        corrected = converted[1]
        assert corrected["expected_answer"] == "Counseling or mental health"
        # Corrected evidence D1:1 maps to session 1's doc.
        assert corrected["ground_truth"] == ["locomo-c00-s01"]
        assert corrected["metadata"]["audit_corrected"] is True
        assert corrected["metadata"]["audit_error_type"] == "HALLUCINATION"

    def test_uncorrected_query_untouched(self, converted):
        untouched = converted[0]
        assert untouched["expected_answer"] == "2022"
        assert untouched["ground_truth"] == ["locomo-c00-s01"]
        assert "audit_corrected" not in untouched["metadata"]

    def test_question_text_mismatch_raises(self, tmp_path):
        dataset = tmp_path / "locomo10.json"
        dataset.write_text(json.dumps(_locomo_blob()), encoding="utf-8")
        bad = _corrections()
        bad[0]["question"] = "A different question entirely?"
        corrections = tmp_path / "corrections.json"
        corrections.write_text(json.dumps(bad), encoding="utf-8")

        with pytest.raises(ValueError, match="does not match dataset question"):
            convert_locomo_to_corpus(
                dataset_path=dataset,
                output_dir=tmp_path / "out",
                audit_corrections_path=corrections,
            )

    def test_no_corrections_path_is_unchanged_behavior(self, tmp_path):
        dataset = tmp_path / "locomo10.json"
        dataset.write_text(json.dumps(_locomo_blob()), encoding="utf-8")
        _, queries_path, _, _ = convert_locomo_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        queries = json.loads(queries_path.read_text())
        assert queries[1]["expected_answer"] == "Psychology, counseling certification"


class TestLoadCorrections:
    def test_load_and_key_by_question_id(self, tmp_path):
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps(_corrections()), encoding="utf-8")
        loaded = load_locomo_corrections(path)
        assert set(loaded) == {"locomo_0_qa1"}

    def test_duplicate_question_id_raises(self, tmp_path):
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps(_corrections() * 2), encoding="utf-8")
        with pytest.raises(ValueError, match="Duplicate correction"):
            load_locomo_corrections(path)

    def test_missing_keys_raises(self, tmp_path):
        path = tmp_path / "corrections.json"
        path.write_text(json.dumps([{"question_id": "locomo_0_qa1"}]), encoding="utf-8")
        with pytest.raises(ValueError, match="missing keys"):
            load_locomo_corrections(path)


class TestSessionDates:
    def test_session_date_in_doc(self, tmp_path):
        blob = _locomo_blob()
        blob[0]["conversation"]["session_1_date_time"] = "1:56 pm on 8 May, 2023"
        dataset = tmp_path / "locomo10.json"
        dataset.write_text(json.dumps(blob), encoding="utf-8")

        docs_dir, _, _, _ = convert_locomo_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        doc = (docs_dir / "locomo-c00-s01.md").read_text()
        assert "session_date: 1:56 pm on 8 May, 2023" in doc
        assert "# Chat session at 1:56 pm on 8 May, 2023" in doc
        assert "title: locomo-c00-s01 (1:56 pm on 8 May, 2023)" in doc

    def test_missing_date_keeps_plain_heading(self, tmp_path):
        dataset = tmp_path / "locomo10.json"
        dataset.write_text(json.dumps(_locomo_blob()), encoding="utf-8")
        docs_dir, _, _, _ = convert_locomo_to_corpus(
            dataset_path=dataset, output_dir=tmp_path / "out"
        )
        doc = (docs_dir / "locomo-c00-s01.md").read_text()
        assert "session_date:" not in doc
        assert "# locomo-c00-s01" in doc
