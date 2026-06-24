"""Tests for the ConvoMem stratified sampler/converter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basic_memory_benchmarks.converters.convomem_to_corpus import (
    convert_convomem_to_corpus,
)


def _case(
    context_size: int,
    conversation_ids: list[str],
    evidence_conv_ids: list[str],
    question: str = "What did I say?",
    answer: str = "You said the thing.",
) -> dict:
    return {
        "contextSize": context_size,
        "conversations": [
            {
                "id": conv_id,
                "containsEvidence": conv_id in evidence_conv_ids,
                "model_name": "gemini-2.5-pro",
                "messages": [
                    {"speaker": "user", "text": f"hello from conversation {index}"},
                    {"speaker": "assistant", "text": "hi there"},
                ],
            }
            for index, conv_id in enumerate(conversation_ids)
        ],
        "evidenceItems": [
            {
                "question": question,
                "answer": answer,
                "category": "Personal Life",
                "conversations": [{"id": conv_id} for conv_id in evidence_conv_ids],
                "message_evidences": [],
            }
        ],
    }


def _write_batch(batches_dir: Path, category: str, level: int, name: str, cases: list[dict]):
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / f"{category}__{level}__{name}.json").write_text(
        json.dumps(cases), encoding="utf-8"
    )


class TestConvertConvomem:
    def test_grouped_output_and_ground_truth(self, tmp_path):
        batches = tmp_path / "batches"
        _write_batch(
            batches,
            "user_evidence",
            1,
            "batched_000",
            [_case(2, ["conv-a", "conv-b"], ["conv-b"])],
        )

        groups_dir, queries_path, doc_count, query_count = convert_convomem_to_corpus(
            batches_dir=batches,
            output_dir=tmp_path / "out",
            sample_per_stratum=10,
            context_sizes=(2,),
        )

        assert doc_count == 2
        assert query_count == 1
        queries = json.loads(queries_path.read_text())
        query = queries[0]
        assert query["category"] == "user_facts"
        assert query["group"].startswith("user_facts-cs2-batched_000-")
        # conv-b was index 1 -> doc id suffix c001.
        assert query["ground_truth"] == [f"{query['group']}-c001"]
        assert query["metadata"]["context_size"] == 2
        assert query["metadata"]["abstention"] is False

    def test_leakage_fields_scrubbed(self, tmp_path):
        batches = tmp_path / "batches"
        _write_batch(
            batches,
            "user_evidence",
            1,
            "batched_000",
            [_case(2, ["conv-a", "conv-b"], ["conv-b"])],
        )
        groups_dir, _, _, _ = convert_convomem_to_corpus(
            batches_dir=batches, output_dir=tmp_path / "out", context_sizes=(2,)
        )

        corpus_text = "".join(path.read_text() for path in groups_dir.rglob("*.md"))
        assert "containsEvidence" not in corpus_text
        assert "gemini" not in corpus_text
        assert "conv-a" not in corpus_text  # raw conversation ids remapped

    def test_sampling_is_deterministic(self, tmp_path):
        batches = tmp_path / "batches"
        cases = [_case(2, [f"conv-{i}-a", f"conv-{i}-b"], [f"conv-{i}-b"]) for i in range(20)]
        _write_batch(batches, "user_evidence", 1, "batched_000", cases)

        ids_by_run = []
        for run in range(2):
            out = tmp_path / f"out{run}"
            _, queries_path, _, _ = convert_convomem_to_corpus(
                batches_dir=batches,
                output_dir=out,
                sample_per_stratum=5,
                seed=42,
                context_sizes=(2,),
            )
            ids_by_run.append([q["id"] for q in json.loads(queries_path.read_text())])

        assert ids_by_run[0] == ids_by_run[1]
        assert len(ids_by_run[0]) == 5

    def test_different_seed_changes_sample(self, tmp_path):
        batches = tmp_path / "batches"
        cases = [_case(2, [f"conv-{i}-a", f"conv-{i}-b"], [f"conv-{i}-b"]) for i in range(20)]
        _write_batch(batches, "user_evidence", 1, "batched_000", cases)

        samples = []
        for seed in (42, 43):
            out = tmp_path / f"out-seed{seed}"
            _, queries_path, _, _ = convert_convomem_to_corpus(
                batches_dir=batches,
                output_dir=out,
                sample_per_stratum=5,
                seed=seed,
                context_sizes=(2,),
            )
            samples.append({q["id"] for q in json.loads(queries_path.read_text())})
        assert samples[0] != samples[1]

    def test_stratification_and_manifest(self, tmp_path):
        batches = tmp_path / "batches"
        _write_batch(
            batches,
            "user_evidence",
            1,
            "batched_000",
            [_case(2, ["a1", "a2"], ["a2"]), _case(4, ["b1", "b2", "b3", "b4"], ["b1"])],
        )
        _write_batch(
            batches,
            "abstention_evidence",
            1,
            "batched_000",
            [_case(2, ["c1", "c2"], [])],
        )

        out = tmp_path / "out"
        _, queries_path, _, query_count = convert_convomem_to_corpus(
            batches_dir=batches, output_dir=out, context_sizes=(2, 4)
        )

        assert query_count == 3
        manifest = json.loads((out / "sampling.json").read_text())
        assert manifest["seed"] == 42
        assert manifest["strata"]["user_facts/cs2"] == {"population": 1, "sampled": 1}
        assert manifest["strata"]["user_facts/cs4"] == {"population": 1, "sampled": 1}
        assert manifest["strata"]["abstention/cs2"] == {"population": 1, "sampled": 1}

        queries = json.loads(queries_path.read_text())
        abstention = [q for q in queries if q["category"] == "abstention"][0]
        # Abstention evidence may reference conversations absent from the
        # haystack; ground truth is simply empty then.
        assert abstention["ground_truth"] == []
        assert abstention["metadata"]["abstention"] is True

    def test_context_size_filter_excludes(self, tmp_path):
        batches = tmp_path / "batches"
        _write_batch(batches, "user_evidence", 1, "batched_000", [_case(2, ["a1"], ["a1"])])
        with pytest.raises(ValueError, match="No ConvoMem cases matched"):
            convert_convomem_to_corpus(
                batches_dir=batches, output_dir=tmp_path / "out", context_sizes=(30,)
            )
