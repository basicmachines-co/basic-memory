"""Tests for answerer-vs-retrieval failure attribution."""

from __future__ import annotations

import json

from basic_memory_benchmarks.models import PerQueryRetrievalResult, QACaseResult
from basic_memory_benchmarks.scoring.diagnose import diagnose_provider, diagnose_run


def _qa(qid, correct, *, provider="bm-local", category="single_hop", abstained=False, error=None):
    return QACaseResult(
        provider=provider,
        query_id=qid,
        category=category,
        question="q?",
        expected_answer="gold",
        generated_answer="ans",
        abstained=abstained,
        correct=correct,
        judge_reason="reason",
        answer_model="claude:haiku",
        judge_model="claude:sonnet",
        answer_latency_ms=1.0,
        answer_input_tokens=1,
        answer_output_tokens=1,
        error=error,
    )


def _retr(qid, recall_at_10, *, provider="bm-local", ground_truth=("d1",)):
    return PerQueryRetrievalResult(
        provider=provider,
        query_id=qid,
        query_text="q?",
        category="single_hop",
        ground_truth=list(ground_truth),
        hits=[],
        recall_at_5=recall_at_10,
        recall_at_10=recall_at_10,
        precision_at_5=0.0,
        mrr=0.0,
        content_hit=False,
        latency_ms=1.0,
    )


def test_attributes_failures_to_answerer_vs_retrieval():
    qa = [
        _qa("q1", True),  # correct
        _qa("q2", False),  # wrong, gold retrieved -> answerer
        _qa("q3", False),  # wrong, gold NOT retrieved -> retrieval
    ]
    retr = [_retr("q1", 1.0), _retr("q2", 1.0), _retr("q3", 0.0)]

    d = diagnose_provider(qa, retr, provider="bm-local")

    assert d.answerable == 3
    assert d.correct == 1
    assert d.retrieved_but_unused == 1
    assert d.truly_missed == 1
    assert d.qa_accuracy == 1 / 3
    assert d.retrieval_ceiling == 2 / 3  # correct + retrieved-but-unused
    assert d.answerer_gap == 1 / 3
    assert d.retrieval_gap == 1 / 3
    assert d.answerer_failure_share == 0.5  # 1 of 2 failures was the answerer


def test_unanswerable_items_excluded_from_attribution():
    qa = [_qa("q1", True), _qa("q2", False)]
    retr = [_retr("q1", 1.0), _retr("q2", 0.0, ground_truth=())]  # q2 has no gold

    d = diagnose_provider(qa, retr, provider="bm-local")

    assert d.answerable == 1
    assert d.unanswerable == 1
    assert d.correct == 1
    assert d.truly_missed == 0  # q2 not counted as a retrieval miss


def test_errored_and_unmatched_are_bucketed_not_attributed():
    qa = [
        _qa("q1", False, error="llm died"),  # errored
        _qa("q2", False),  # no retrieval row -> unmatched
    ]
    retr = [_retr("q1", 0.0)]

    d = diagnose_provider(qa, retr, provider="bm-local")

    assert d.total_cases == 2
    assert d.errored == 1
    assert d.unmatched == 1
    assert d.answerable == 0
    assert d.qa_accuracy == 0.0  # no division by zero


def test_per_category_breakdown():
    qa = [
        _qa("q1", True, category="single_hop"),
        _qa("q2", False, category="multi_hop"),  # retrieved
        _qa("q3", False, category="multi_hop"),  # missed
    ]
    retr = [_retr("q1", 1.0), _retr("q2", 1.0), _retr("q3", 0.0)]

    d = diagnose_provider(qa, retr, provider="bm-local")

    assert d.by_category["single_hop"].correct == 1
    assert d.by_category["multi_hop"].answerable == 2
    assert d.by_category["multi_hop"].retrieved_but_unused == 1
    assert d.by_category["multi_hop"].truly_missed == 1


def test_recall_field_is_honored():
    qa = [_qa("q1", False)]
    # gold in top-10 but not top-5: answerer failure under r@10, retrieval miss under r@5
    retr = [
        PerQueryRetrievalResult(
            provider="bm-local",
            query_id="q1",
            query_text="q?",
            category="single_hop",
            ground_truth=["d1"],
            hits=[],
            recall_at_5=0.0,
            recall_at_10=1.0,
            precision_at_5=0.0,
            mrr=0.0,
            content_hit=False,
            latency_ms=1.0,
        )
    ]

    assert diagnose_provider(qa, retr, provider="bm-local").truly_missed == 0
    assert (
        diagnose_provider(qa, retr, provider="bm-local", recall_field="recall_at_5").truly_missed
        == 1
    )


def test_diagnose_run_covers_all_providers_in_stable_order():
    qa = [_qa("q1", True, provider="bm-local"), _qa("q1", False, provider="mem0-local")]
    retr = [_retr("q1", 1.0, provider="bm-local"), _retr("q1", 1.0, provider="mem0-local")]

    diags = diagnose_run(qa, retr)

    assert [d.provider for d in diags] == ["bm-local", "mem0-local"]
    assert diags[0].correct == 1
    assert diags[1].retrieved_but_unused == 1


def test_diagnose_stage_writes_json(tmp_path):
    from basic_memory_benchmarks import runner as runner_module

    (tmp_path / "per-query-qa.jsonl").write_text(
        "\n".join(
            json.dumps(c.model_dump(mode="json"))
            for c in [_qa("q1", True), _qa("q2", False), _qa("q3", False)]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "per-query-retrieval.jsonl").write_text(
        "\n".join(
            json.dumps(r.model_dump(mode="json"))
            for r in [_retr("q1", 1.0), _retr("q2", 1.0), _retr("q3", 0.0)]
        )
        + "\n",
        encoding="utf-8",
    )

    out = runner_module.run_diagnose_stage(run_dir=tmp_path, source="qa")
    assert out.name == "qa-diagnosis.json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    prov = payload["providers"][0]
    assert prov["retrieved_but_unused"] == 1
    assert prov["truly_missed"] == 1


def test_diagnose_stage_prefers_rejudge_when_auto(tmp_path):
    from basic_memory_benchmarks import runner as runner_module

    (tmp_path / "per-query-retrieval.jsonl").write_text(
        json.dumps(_retr("q1", 1.0).model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    # Original judged it wrong; rejudge flips it to correct.
    (tmp_path / "per-query-qa.jsonl").write_text(
        json.dumps(_qa("q1", False).model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    (tmp_path / "per-query-qa-rejudge.jsonl").write_text(
        json.dumps(_qa("q1", True).model_dump(mode="json")) + "\n", encoding="utf-8"
    )

    out = runner_module.run_diagnose_stage(run_dir=tmp_path, source="auto")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"] == "per-query-qa-rejudge.jsonl"
    assert payload["providers"][0]["correct"] == 1
