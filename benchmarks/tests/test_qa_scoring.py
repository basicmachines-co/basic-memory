"""Tests for the end-to-end QA scoring stage."""

from __future__ import annotations

import json

import pytest

from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner, LLMRunnerError
from basic_memory_benchmarks.models import PerQueryRetrievalResult
from basic_memory_benchmarks.scoring.qa import (
    ABSTAIN_SENTINEL,
    build_answer_prompt,
    build_judge_prompt,
    parse_judge_verdict,
    run_qa,
)


class FakeRunner(LLMRunner):
    """Returns canned responses keyed by substring match against the prompt."""

    def __init__(self, responses: dict[str, str], default: str = ""):
        self.spec = "fake:test"
        self.responses = responses
        self.default = default
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        for needle, response in self.responses.items():
            if needle in prompt:
                return LLMResult(
                    text=response, model="fake", input_tokens=10, output_tokens=5, latency_ms=1.0
                )
        if self.default:
            return LLMResult(
                text=self.default, model="fake", input_tokens=10, output_tokens=5, latency_ms=1.0
            )
        raise LLMRunnerError(f"No canned response for prompt: {prompt[:80]}")


def _row(
    query_id: str,
    question: str,
    expected: str | None,
    context: str,
    category: str = "single_hop",
) -> PerQueryRetrievalResult:
    return PerQueryRetrievalResult(
        provider="bm-local",
        query_id=query_id,
        query_text=question,
        category=category,
        ground_truth=[],
        expected_answer=expected,
        recall_at_5=0.0,
        recall_at_10=0.0,
        precision_at_5=0.0,
        mrr=0.0,
        content_hit=False,
        latency_ms=1.0,
        retrieved_context=context,
    )


class TestPrompts:
    def test_answer_prompt_includes_question_and_context(self):
        prompt = build_answer_prompt("Where does Joanna live?", "Joanna lives in Austin.")
        assert "Where does Joanna live?" in prompt
        assert "Joanna lives in Austin." in prompt
        assert ABSTAIN_SENTINEL in prompt

    def test_answer_prompt_marks_empty_context(self):
        prompt = build_answer_prompt("Where does Joanna live?", "   ")
        assert "(no memories were retrieved)" in prompt

    def test_judge_prompt_includes_all_parts(self):
        prompt = build_judge_prompt("Q?", "gold fact", "candidate fact")
        assert "Q?" in prompt
        assert "gold fact" in prompt
        assert "candidate fact" in prompt

    def test_judge_prompt_allows_incomplete_gold(self):
        # The rubric must tell the judge gold answers may be incomplete and that
        # extra non-contradicting facts are not errors (LoCoMo gold answers are
        # documented-incomplete; the prior rubric over-failed correct answers).
        prompt = build_judge_prompt("Q?", "gold", "candidate")
        assert "INCOMPLETE" in prompt
        assert "not errors" in prompt.lower()

    def test_judge_prompt_handles_unanswerable_before_fact_matching(self):
        # Abstention/unanswerable cases (ConvoMem abstention, LoCoMo adversarial)
        # must be judged FIRST on whether the candidate declines — otherwise the
        # fact-matching rules wrongly fail a correct "I don't know" against a
        # "no information" gold (regression caught during number regeneration).
        prompt = build_judge_prompt("Q?", "gold", "candidate")
        first = prompt.split("OTHERWISE")[0]
        assert "not available" in first.lower()
        assert ABSTAIN_SENTINEL in first
        assert "declines" in first.lower()


class TestParseJudgeVerdict:
    def test_plain_json(self):
        correct, reason = parse_judge_verdict('{"correct": true, "reason": "matches"}')
        assert correct is True
        assert reason == "matches"

    def test_json_in_code_fence(self):
        raw = 'Here is my verdict:\n```json\n{"correct": false, "reason": "missing fact"}\n```'
        correct, reason = parse_judge_verdict(raw)
        assert correct is False
        assert reason == "missing fact"

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_judge_verdict("The answer is correct.")

    def test_non_boolean_correct_raises(self):
        with pytest.raises(ValueError):
            parse_judge_verdict('{"correct": "yes"}')


class TestRunQA:
    def test_correct_and_incorrect_cases(self):
        rows = [
            _row("q1", "Where does Joanna live?", "Austin", "Joanna lives in Austin."),
            _row("q2", "What is Anthony's job?", "Engineer", "Anthony enjoys hiking."),
        ]
        answerer = FakeRunner(
            {
                "Where does Joanna live?": "Austin",
                "What is Anthony's job?": ABSTAIN_SENTINEL,
            }
        )
        judge = FakeRunner(
            {
                "Candidate answer: Austin": '{"correct": true, "reason": "match"}',
                f"Candidate answer: {ABSTAIN_SENTINEL}": '{"correct": false, "reason": "abstained on answerable"}',
            }
        )

        cases, summary = run_qa(
            rows, provider="bm-local", answerer=answerer, judge=judge, max_workers=1
        )

        assert summary.total_cases == 2
        assert summary.correct_count == 1
        assert summary.accuracy == 0.5
        assert summary.abstain_count == 1
        by_id = {case.query_id: case for case in cases}
        assert by_id["q1"].correct is True
        assert by_id["q2"].correct is False
        assert by_id["q2"].abstained is True

    def test_category_breakdown(self):
        rows = [
            _row("q1", "Q1?", "A1", "ctx", category="single_hop"),
            _row("q2", "Q2?", "A2", "ctx", category="temporal"),
        ]
        answerer = FakeRunner({}, default="some answer")
        judge = FakeRunner(
            {
                "Q1?": '{"correct": true, "reason": "ok"}',
                "Q2?": '{"correct": false, "reason": "wrong"}',
            }
        )
        _, summary = run_qa(
            rows, provider="bm-local", answerer=answerer, judge=judge, max_workers=1
        )

        assert summary.by_category["single_hop"].accuracy == 1.0
        assert summary.by_category["temporal"].accuracy == 0.0

    def test_rows_without_expected_answer_are_skipped(self):
        rows = [_row("q1", "Q1?", None, "ctx")]
        answerer = FakeRunner({})
        judge = FakeRunner({})
        cases, summary = run_qa(rows, provider="bm-local", answerer=answerer, judge=judge)
        assert cases == []
        assert summary.skipped_reason is not None

    def test_runner_error_recorded_not_raised(self):
        rows = [
            _row("q1", "Q1?", "A1", "ctx"),
            _row("q2", "Q2?", "A2", "ctx"),
        ]
        answerer = FakeRunner({"Q2?": "answer two"})  # Q1 has no canned response -> error
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')

        cases, summary = run_qa(
            rows, provider="bm-local", answerer=answerer, judge=judge, max_workers=1
        )

        by_id = {case.query_id: case for case in cases}
        assert by_id["q1"].error is not None
        assert by_id["q1"].correct is False
        assert by_id["q2"].correct is True
        assert summary.error_count == 1
        assert summary.total_cases == 2

    def test_token_and_latency_accounting(self):
        rows = [_row("q1", "Q1?", "A1", "ctx")]
        answerer = FakeRunner({}, default="answer")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        _, summary = run_qa(rows, provider="bm-local", answerer=answerer, judge=judge)
        assert summary.total_answer_input_tokens == 10
        assert summary.total_answer_output_tokens == 5
        assert summary.mean_answer_latency_ms == pytest.approx(1.0)


class TestQAStageArtifacts:
    def test_run_qa_stage_writes_artifacts(self, tmp_path, monkeypatch):
        from basic_memory_benchmarks import runner as runner_module

        rows = [
            _row("q1", "Where does Joanna live?", "Austin", "Joanna lives in Austin."),
        ]
        retrieval_path = tmp_path / "per-query-retrieval.jsonl"
        with retrieval_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row.model_dump(mode="json")) + "\n")

        fake = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: fake)

        runner_module.run_qa_stage(
            run_dir=tmp_path,
            answerer_spec="fake:test",
            judge_spec="fake:test",
            max_workers=1,
        )

        assert (tmp_path / "per-query-qa.jsonl").exists()
        summary = json.loads((tmp_path / "qa-summary.json").read_text())
        assert summary["providers"][0]["provider"] == "bm-local"
        assert summary["providers"][0]["total_cases"] == 1


class TestQuestionDate:
    def test_question_date_reaches_answerer_and_judge(self):
        row = _row("q1", "How many weeks ago did I visit the dentist?", "Three weeks ago", "ctx")
        row = row.model_copy(update={"metadata": {"question_date": "2023/05/30 (Tue) 23:40"}})
        answerer = FakeRunner({}, default="Three weeks ago")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')

        run_qa([row], provider="bm-local", answerer=answerer, judge=judge, max_workers=1)

        assert "question asked on 2023/05/30 (Tue) 23:40" in answerer.prompts[0]
        assert "question asked on 2023/05/30 (Tue) 23:40" in judge.prompts[0]

    def test_no_date_means_plain_question(self):
        row = _row("q1", "Where does Joanna live?", "Austin", "ctx")
        answerer = FakeRunner({}, default="Austin")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')

        run_qa([row], provider="bm-local", answerer=answerer, judge=judge, max_workers=1)

        assert "question asked on" not in answerer.prompts[0]


class TestAssembleContext:
    def _hit(self, doc_id: str, text: str):
        from basic_memory_benchmarks.models import SearchHit

        return SearchHit(source_doc_id=doc_id, text=text, score=1.0)

    def test_sections_numbered_with_source(self):
        from basic_memory_benchmarks.scoring.qa import assemble_context

        ctx = assemble_context(
            [self._hit("doc-a", "alpha facts"), self._hit("doc-b", "beta facts")]
        )
        assert "[Memory 1 | source: doc-a]" in ctx
        assert "alpha facts" in ctx
        assert "[Memory 2 | source: doc-b]" in ctx

    def test_budget_caps_total(self):
        from basic_memory_benchmarks.scoring.qa import CONTEXT_MAX_CHARS, assemble_context

        hits = [self._hit(f"doc-{i}", "x" * 3000) for i in range(10)]
        ctx = assemble_context(hits)
        # Section text alone stays within the global budget (headers excluded).
        text_chars = sum(len(part.split("]\n", 1)[1]) for part in ctx.split("\n\n"))
        assert text_chars <= CONTEXT_MAX_CHARS

    def test_per_hit_cap_with_many_hits(self):
        from basic_memory_benchmarks.scoring.qa import CONTEXT_CHARS_PER_HIT, assemble_context

        # With a full hit list the per-hit cap is the slice constant; a lone
        # hit instead gets the whole budget (see TestContextBudgetOverride).
        hits = [self._hit(f"doc-{i}", "z" * (CONTEXT_CHARS_PER_HIT + 500)) for i in range(10)]
        ctx = assemble_context(hits)
        first_section = ctx.split("\n\n")[0]
        assert first_section.count("z") == CONTEXT_CHARS_PER_HIT

    def test_empty_hits_skipped(self):
        from basic_memory_benchmarks.scoring.qa import assemble_context

        ctx = assemble_context([self._hit("doc-a", ""), self._hit("doc-b", "real")])
        assert "[Memory 2 | source: doc-b]" in ctx
        assert "doc-a" not in ctx

    def test_qa_uses_assembled_context(self):
        from basic_memory_benchmarks.models import SearchHit

        row = _row("q1", "Where does Joanna live?", "Austin", "legacy joined context")
        row = row.model_copy(
            update={
                "hits": [
                    SearchHit(source_doc_id="doc-a", text="Joanna lives in Austin.", score=1.0)
                ]
            }
        )
        answerer = FakeRunner({}, default="Austin")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        run_qa([row], provider="bm-local", answerer=answerer, judge=judge, max_workers=1)
        assert "[Memory 1 | source: doc-a]" in answerer.prompts[0]
        assert "legacy joined context" not in answerer.prompts[0]

    def test_fallback_to_legacy_context_without_hits(self):
        row = _row("q1", "Where does Joanna live?", "Austin", "legacy joined context")
        answerer = FakeRunner({}, default="Austin")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        run_qa([row], provider="bm-local", answerer=answerer, judge=judge, max_workers=1)
        assert "legacy joined context" in answerer.prompts[0]


class TestPromptCharsAccounting:
    def test_prompt_chars_recorded(self):
        rows = [_row("q1", "Q1?", "A1", "some retrieved context here")]
        answerer = FakeRunner({}, default="answer")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        cases, summary = run_qa(rows, provider="bm-local", answerer=answerer, judge=judge)
        assert cases[0].answer_prompt_chars == len(answerer.prompts[0])
        assert summary.mean_answer_prompt_chars == cases[0].answer_prompt_chars


class TestContextBudgetOverride:
    def _hit(self, doc_id: str, text: str):
        from basic_memory_benchmarks.models import SearchHit

        return SearchHit(source_doc_id=doc_id, text=text, score=1.0)

    def test_single_hit_uses_full_budget(self):
        from basic_memory_benchmarks.scoring.qa import assemble_context

        # One massive hit (full-context baseline) gets the whole budget,
        # not the per-hit slice.
        ctx = assemble_context([self._hit("all", "z" * 50_000)], max_chars=40_000)
        assert ctx.count("z") == 40_000

    def test_budget_override_flows_through_run_qa(self):
        from basic_memory_benchmarks.models import SearchHit

        row = _row("q1", "Q?", "A", "legacy")
        row = row.model_copy(
            update={"hits": [SearchHit(source_doc_id="all", text="z" * 50_000, score=1.0)]}
        )
        answerer = FakeRunner({}, default="answer")
        judge = FakeRunner({}, default='{"correct": true, "reason": "ok"}')
        run_qa(
            [row],
            provider="p",
            answerer=answerer,
            judge=judge,
            max_workers=1,
            max_context_chars=30_000,
        )
        assert answerer.prompts[0].count("z") == 30_000


class TestHitTitleInContext:
    def test_title_metadata_lands_in_header(self):
        from basic_memory_benchmarks.models import SearchHit
        from basic_memory_benchmarks.scoring.qa import assemble_context

        hit = SearchHit(
            source_doc_id="doc-a",
            text="- **Melanie:** I hiked yesterday!",
            score=1.0,
            metadata={"title": "locomo-c00-s18 (3:01 pm on 20 October, 2023)"},
        )
        ctx = assemble_context([hit])
        assert "| locomo-c00-s18 (3:01 pm on 20 October, 2023)]" in ctx

    def test_title_skipped_when_already_in_text(self):
        from basic_memory_benchmarks.models import SearchHit
        from basic_memory_benchmarks.scoring.qa import assemble_context

        hit = SearchHit(
            source_doc_id="doc-a",
            text="# Chat session at 8 May 2023\nfull body",
            score=1.0,
            metadata={"title": "Chat session at 8 May 2023"},
        )
        ctx = assemble_context([hit])
        assert ctx.count("Chat session at 8 May 2023") == 1


class TestRejudge:
    def _case(self, qid, correct, generated="ans", error=None, category="single_hop"):
        from basic_memory_benchmarks.models import QACaseResult

        return QACaseResult(
            provider="bm-local",
            query_id=qid,
            category=category,
            question="Q?",
            expected_answer="gold",
            generated_answer=generated,
            abstained=False,
            correct=correct,
            judge_reason="orig",
            answer_model="claude:haiku",
            judge_model="claude:sonnet",
            answer_latency_ms=1.0,
            answer_input_tokens=1,
            answer_output_tokens=1,
            error=error,
        )

    def test_rejudge_flips_and_summary(self):
        from basic_memory_benchmarks.scoring.qa import rejudge_cases

        cases = [self._case("q1", correct=False), self._case("q2", correct=True)]
        # New judge flips q1 to correct, keeps q2 correct.
        judge = FakeRunner(
            {},
            default='{"correct": true, "reason": "incomplete gold is fine"}',
        )
        rejudged, summary, flips = rejudge_cases(cases, judge=judge, max_workers=1)

        assert summary.correct_count == 2
        assert summary.accuracy == 1.0
        assert len(flips) == 1
        assert flips[0]["query_id"] == "q1"
        assert flips[0]["was_correct"] is False and flips[0]["now_correct"] is True
        # answerer fields preserved; judge_model updated.
        assert rejudged[0].judge_model == "fake:test"
        assert rejudged[0].answer_model == "claude:haiku"

    def test_rejudge_preserves_errored_cases(self):
        from basic_memory_benchmarks.scoring.qa import rejudge_cases

        cases = [self._case("q1", correct=False, error="llm died")]
        judge = FakeRunner({}, default='{"correct": true, "reason": "x"}')
        rejudged, summary, flips = rejudge_cases(cases, judge=judge, max_workers=1)
        # Errored case is untouched (no generated answer to judge).
        assert rejudged[0].correct is False
        assert rejudged[0].error == "llm died"
        assert flips == []

    def test_rejudge_empty(self):
        from basic_memory_benchmarks.scoring.qa import rejudge_cases

        judge = FakeRunner({}, default='{"correct": true, "reason": "x"}')
        rejudged, summary, flips = rejudge_cases([], judge=judge)
        assert rejudged == [] and flips == []
        assert summary.skipped_reason is not None

    def test_rejudge_stage_artifacts(self, tmp_path, monkeypatch):
        import json as _json

        from basic_memory_benchmarks import runner as runner_module

        case = self._case("q1", correct=False)
        (tmp_path / "per-query-qa.jsonl").write_text(
            _json.dumps(case.model_dump(mode="json")) + "\n", encoding="utf-8"
        )
        fake = FakeRunner({}, default='{"correct": true, "reason": "flipped"}')
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: fake)
        runner_module.run_rejudge_stage(run_dir=tmp_path, judge_spec="fake:test", max_workers=1)

        assert (tmp_path / "per-query-qa-rejudge.jsonl").exists()
        summary = _json.loads((tmp_path / "qa-rejudge-summary.json").read_text())
        assert summary["providers"][0]["correct_count"] == 1
        flips = _json.loads((tmp_path / "qa-rejudge-flips.json").read_text())
        assert flips["flip_count"] == 1
