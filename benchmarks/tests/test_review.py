"""Tests for the judge-review HTML report."""

from __future__ import annotations

import json

from basic_memory_benchmarks.models import QACaseResult
from basic_memory_benchmarks.scoring.review import build_review_html


def _case(qid, correct, *, abstained=False, error=None, generated="ans", category="single_hop"):
    return QACaseResult(
        provider="bm-local",
        query_id=qid,
        category=category,
        question="What did <b>Joanna</b> say?",  # markup to test escaping
        expected_answer="gold & more",
        generated_answer=generated,
        abstained=abstained,
        correct=correct,
        judge_reason="reason with <tag>",
        answer_model="claude:haiku",
        judge_model="claude:sonnet",
        answer_latency_ms=1.0,
        answer_input_tokens=1,
        answer_output_tokens=1,
        error=error,
    )


def test_report_renders_and_embeds_cases():
    html_out = build_review_html([_case("q1", True), _case("q2", False)], run_id="myrun")
    assert "<!doctype html>" in html_out.lower()
    assert "Judge review — myrun" in html_out
    assert "claude:sonnet" in html_out  # judge model surfaced
    # Data embedded as a JSON blob the page parses.
    blob = html_out.split('id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    cases = json.loads(blob)
    assert {c["query_id"] for c in cases} == {"q1", "q2"}


def test_report_escapes_user_text():
    html_out = build_review_html([_case("q1", True)], run_id="r")
    # Raw markup from question/gold/reason must be escaped, not live HTML.
    assert "<b>Joanna</b>" not in html_out
    assert "&lt;b&gt;Joanna&lt;/b&gt;" in html_out
    assert "gold &amp; more" in html_out
    assert "&lt;tag&gt;" in html_out


def test_report_handles_empty_and_errored_cases():
    html_out = build_review_html([_case("q1", False, error="llm died", generated="")], run_id="r")
    blob = html_out.split('id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    case = json.loads(blob)[0]
    assert case["error"] == "llm died"
    assert case["generated_answer"] == ""


def test_report_empty_run():
    html_out = build_review_html([], run_id="empty")
    assert "0 decisions" in html_out
    assert "n/a" in html_out  # no judge model


def test_review_stage_writes_html(tmp_path):
    from basic_memory_benchmarks import runner as runner_module

    case = _case("q1", True)
    (tmp_path / "per-query-qa.jsonl").write_text(
        json.dumps(case.model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    out = runner_module.run_review_stage(run_dir=tmp_path, source="qa")
    assert out.name == "review.html"
    assert "Judge review" in out.read_text(encoding="utf-8")


def test_review_stage_prefers_rejudge_when_auto(tmp_path):
    from basic_memory_benchmarks import runner as runner_module

    (tmp_path / "per-query-qa.jsonl").write_text(
        json.dumps(_case("orig", True).model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    (tmp_path / "per-query-qa-rejudge.jsonl").write_text(
        json.dumps(_case("rejudged", True).model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    out = runner_module.run_review_stage(run_dir=tmp_path, source="auto")
    text = out.read_text(encoding="utf-8")
    assert "rejudged" in text and "orig" not in text
