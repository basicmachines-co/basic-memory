"""Tests for BEAM nugget/ordering scoring, aggregation, and the score stage."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean

import pytest

from basic_memory_benchmarks.datasets.beam import ABILITY_KEYS
from basic_memory_benchmarks.models import (
    BeamCaseScore,
    BeamOrderingScore,
    PerQueryRetrievalResult,
    QACaseResult,
)
from basic_memory_benchmarks.scoring.beam import (
    JudgeUsage,
    _align_with_judge,
    event_ordering_score,
    kendall_tau_b,
    parse_nugget_verdict,
    score_beam_cases,
    score_nuggets,
    summarize_beam_cases,
)
from basic_memory_benchmarks.scoring.retrieval import summarize_provider
from test_qa_scoring import FakeRunner


def _retrieval_row(
    query_id: str,
    ability: str,
    rubric: list[str] | None,
    question: str = "Q?",
    provider: str = "bm-local",
) -> PerQueryRetrievalResult:
    metadata: dict[str, object] = {}
    if rubric is not None:
        metadata = {"ability": ability, "rubric": rubric}
    return PerQueryRetrievalResult(
        provider=provider,
        query_id=query_id,
        query_text=question,
        category=ability,
        ground_truth=[],
        expected_answer="gold",
        recall_at_5=0.0,
        recall_at_10=0.0,
        precision_at_5=0.0,
        mrr=0.0,
        content_hit=False,
        latency_ms=1.0,
        retrieved_context="ctx",
        metadata=metadata,
    )


def _qa_case(
    query_id: str,
    generated: str,
    error: str | None = None,
    provider: str = "bm-local",
    category: str = "information_extraction",
) -> QACaseResult:
    return QACaseResult(
        provider=provider,
        query_id=query_id,
        category=category,
        question="Q?",
        expected_answer="gold",
        generated_answer=generated,
        abstained=False,
        correct=False,
        judge_reason="binary",
        answer_model="fake:answerer",
        judge_model="fake:judge",
        answer_latency_ms=2.0,
        answer_input_tokens=100,
        answer_output_tokens=20,
        answer_prompt_chars=500,
        error=error,
    )


def _case_score(
    query_id: str,
    ability: str,
    *,
    ability_score: float = 0.0,
    nugget_score: float = 0.0,
    ordering: BeamOrderingScore | None = None,
    error: str | None = None,
) -> BeamCaseScore:
    return BeamCaseScore(
        provider="bm-local",
        query_id=query_id,
        ability=ability,
        question="Q?",
        generated_answer="answer",
        nugget_score=nugget_score,
        ordering=ordering,
        ability_score=ability_score,
        answer_input_tokens=100,
        answer_output_tokens=20,
        answer_prompt_chars=400,
        answer_latency_ms=2.0,
        judge_input_tokens=30,
        judge_output_tokens=15,
        judge_calls=3,
        judge_model="fake:test",
        error=error,
    )


class TestKendallTauB:
    def test_perfect_agreement(self) -> None:
        assert kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)

    def test_perfect_reversal(self) -> None:
        assert kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)

    def test_tie_correction_hand_computed(self) -> None:
        # 5 concordant pairs, 0 discordant, 1 y-tie out of 6 pairs:
        # tau_b = 5 / sqrt(6 * 5).
        assert kendall_tau_b([1, 2, 3, 4], [1, 2, 2, 4]) == pytest.approx(5 / math.sqrt(30))

    def test_scipy_documented_example(self) -> None:
        # scipy.stats.kendalltau doc example: tau == -0.47140452079103173.
        tau = kendall_tau_b([12, 2, 1, 12, 2], [1, 4, 7, 1, 0])
        assert tau == pytest.approx(-0.47140452079103173)

    def test_all_ties_returns_none(self) -> None:
        assert kendall_tau_b([1, 1, 1], [1, 2, 3]) is None

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="differ in length"):
            kendall_tau_b([1, 2], [1, 2, 3])


class TestParseNuggetVerdict:
    def test_plain_json(self) -> None:
        score, reason = parse_nugget_verdict('{"score": 0.5, "reason": "partial"}')
        assert score == 0.5
        assert reason == "partial"

    def test_json_in_prose_and_fence(self) -> None:
        raw = 'My verdict follows:\n```json\n{"score": 1.0, "reason": "stated"}\n```\nDone.'
        score, reason = parse_nugget_verdict(raw)
        assert score == 1.0
        assert reason == "stated"

    def test_integer_score_accepted(self) -> None:
        score, _ = parse_nugget_verdict('{"score": 1, "reason": "ok"}')
        assert score == 1.0

    def test_score_outside_scale_raises(self) -> None:
        with pytest.raises(ValueError, match="must be 0, 0.5 or 1"):
            parse_nugget_verdict('{"score": 0.7, "reason": "close"}')

    def test_missing_score_raises(self) -> None:
        with pytest.raises(ValueError, match="must be 0, 0.5 or 1"):
            parse_nugget_verdict('{"reason": "no score at all"}')

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="no JSON object"):
            parse_nugget_verdict("The response satisfies the rubric.")


class TestScoreNuggets:
    def test_zero_half_one_averaging(self) -> None:
        judge = FakeRunner(
            {
                "States March 29": '{"score": 1.0, "reason": "stated"}',
                "States the salary": '{"score": 0.5, "reason": "partial"}',
                "Mentions the puppy": '{"score": 0.0, "reason": "missing"}',
            }
        )
        usage = JudgeUsage()

        verdicts = score_nuggets(
            "What happened?",
            ["States March 29", "States the salary", "Mentions the puppy"],
            "March 29 and some salary talk",
            judge,
            usage,
        )

        assert [verdict.score for verdict in verdicts] == [1.0, 0.5, 0.0]
        assert [verdict.nugget for verdict in verdicts] == [
            "States March 29",
            "States the salary",
            "Mentions the puppy",
        ]
        assert mean(verdict.score for verdict in verdicts) == 0.5
        # One judge call per nugget; FakeRunner charges 10 in / 5 out per call.
        assert usage.calls == 3
        assert usage.input_tokens == 30
        assert usage.output_tokens == 15

    def test_question_rubric_and_response_reach_judge(self) -> None:
        judge = FakeRunner({}, default='{"score": 1.0, "reason": "ok"}')
        usage = JudgeUsage()

        score_nuggets(
            "When is the dentist appointment?",
            ["States March 29"],
            "It is on March 29.",
            judge,
            usage,
        )

        prompt = judge.prompts[0]
        # The question is injected (upstream-bug fix in the adapted prompt).
        assert "QUESTION (what the response was asked): When is the dentist appointment?" in prompt
        assert "RUBRIC CRITERION (what to check): States March 29" in prompt
        assert "RESPONSE TO EVALUATE: It is on March 29." in prompt


class TestAlignWithJudge:
    def test_greedy_first_unused_replacement(self) -> None:
        reference = ["Alpha event", "Beta event", "Gamma event"]
        system = ["The beta thing happened", "gamma occurred", "unrelated noise"]
        judge = FakeRunner(
            {
                "First snippet: Beta event\n\nSecond snippet: The beta thing happened": "YES",
                "First snippet: Gamma event\n\nSecond snippet: gamma occurred": "YES",
            },
            default="NO",
        )
        usage = JudgeUsage()

        aligned = _align_with_judge(reference, system, judge, usage)

        # Matched lines are replaced by the reference strings; unmatched pass
        # through. Used reference items are skipped (greedy first-unused).
        assert aligned == ["Beta event", "Gamma event", "unrelated noise"]
        # sys0: Alpha NO + Beta YES; sys1: Alpha NO + Gamma YES (Beta used,
        # skipped); sys2: Alpha NO (Beta/Gamma used) => 5 calls.
        assert usage.calls == 5


class TestEventOrderingScore:
    def test_perfect_order(self) -> None:
        reference = ["A rocket launched", "A satellite deployed"]
        judge = FakeRunner(
            {
                "First snippet: A rocket launched\n\nSecond snippet: A rocket launched": "YES",
                "First snippet: A satellite deployed\n\nSecond snippet: A satellite deployed": (
                    "YES"
                ),
            },
            default="NO",
        )

        result = event_ordering_score(reference, list(reference), judge, JudgeUsage())

        assert result.precision == 1.0
        assert result.recall == 1.0
        assert result.f1 == 1.0
        assert result.tau_norm == 1.0
        assert result.final_score == 1.0

    def test_reversed_order_zeroes_tau_norm(self) -> None:
        reference = ["First event", "Second event"]
        judge = FakeRunner(
            {
                "First snippet: First event\n\nSecond snippet: First event": "YES",
                "First snippet: Second event\n\nSecond snippet: Second event": "YES",
            },
            default="NO",
        )

        result = event_ordering_score(
            reference, ["Second event", "First event"], judge, JudgeUsage()
        )

        # All events found (f1 = 1) but fully reversed: tau = -1 -> tau_norm 0.
        assert result.f1 == 1.0
        assert result.tau_norm == 0.0
        assert result.final_score == 0.0

    def test_partial_alignment_math(self) -> None:
        reference = ["Alpha event", "Beta event", "Gamma event"]
        system = ["The beta thing happened", "gamma occurred", "unrelated noise"]
        judge = FakeRunner(
            {
                "First snippet: Beta event\n\nSecond snippet: The beta thing happened": "YES",
                "First snippet: Gamma event\n\nSecond snippet: gamma occurred": "YES",
            },
            default="NO",
        )

        result = event_ordering_score(reference, system, judge, JudgeUsage())

        # tp=2 (Beta, Gamma), fp=1 (noise), fn=1 (Alpha) -> P = R = F1 = 2/3.
        assert result.precision == pytest.approx(2 / 3)
        assert result.recall == pytest.approx(2 / 3)
        assert result.f1 == pytest.approx(2 / 3)
        # Union [Alpha, Beta, Gamma, noise], tie rank 5: ref ranks [1,2,3,5],
        # system ranks [5,1,2,3] -> 3 concordant, 3 discordant -> tau = 0.
        assert result.tau_norm == pytest.approx(0.5)
        assert result.final_score == pytest.approx(1 / 3)


class TestScoreBeamCases:
    def test_nugget_case_scoring_and_token_accounting(self) -> None:
        rows = [
            _retrieval_row(
                "q1",
                "information_extraction",
                ["States March 29", "Mentions Biscuit"],
                question="When is the dentist appointment?",
            )
        ]
        qa_cases = [_qa_case("q1", "March 29, no pets discussed")]
        judge = FakeRunner(
            {
                "States March 29": '{"score": 1.0, "reason": "stated"}',
                "Mentions Biscuit": '{"score": 0.0, "reason": "absent"}',
            }
        )

        cases, summaries = score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

        case = cases[0]
        assert case.ability == "information_extraction"
        assert case.question == "When is the dentist appointment?"
        assert case.nugget_score == 0.5
        assert case.ability_score == 0.5
        assert case.ordering is None
        assert case.error is None
        # Answer-side numbers copied from the QA case; judge-side summed here.
        assert case.answer_input_tokens == 100
        assert case.answer_output_tokens == 20
        assert case.answer_prompt_chars == 500
        assert case.answer_latency_ms == 2.0
        assert case.judge_calls == 2
        assert case.judge_input_tokens == 20
        assert case.judge_output_tokens == 10
        assert case.judge_model == "fake:test"
        assert summaries[0].provider == "bm-local"

    def test_event_ordering_ability_score_is_tau_norm(self) -> None:
        rows = [_retrieval_row("q1", "event_ordering", ["First event", "Second event"])]
        qa_cases = [_qa_case("q1", "Second event\nFirst event", category="event_ordering")]
        judge = FakeRunner(
            {
                "what to check): First event": '{"score": 1.0, "reason": "present"}',
                "what to check): Second event": '{"score": 1.0, "reason": "present"}',
                "First snippet: First event\n\nSecond snippet: First event": "YES",
                "First snippet: Second event\n\nSecond snippet: Second event": "YES",
            },
            default="NO",
        )

        cases, summaries = score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

        case = cases[0]
        assert case.nugget_score == 1.0  # both nuggets judged present
        assert case.ordering is not None
        assert case.ordering.f1 == 1.0
        assert case.ordering.tau_norm == 0.0  # reversed order
        # The ability headline is tau_norm, not the nugget score.
        assert case.ability_score == 0.0
        # 2 nugget calls + 3 alignment calls.
        assert case.judge_calls == 5
        assert summaries[0].by_ability["event_ordering"].mean_score == 0.0
        assert summaries[0].by_ability["event_ordering"].mean_nugget_score == 1.0

    def test_errored_qa_case_is_never_judged(self) -> None:
        rows = [_retrieval_row("q1", "information_extraction", ["nugget"])]
        qa_cases = [_qa_case("q1", "", error="answerer died")]
        judge = FakeRunner({})  # would raise if called

        cases, summaries = score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

        assert cases[0].error == "answerer died"
        assert cases[0].nugget_verdicts == []
        assert cases[0].judge_calls == 0
        assert judge.prompts == []
        assert summaries[0].error_count == 1

    def test_empty_answer_becomes_explicit_error(self) -> None:
        rows = [_retrieval_row("q1", "information_extraction", ["nugget"])]
        qa_cases = [_qa_case("q1", "")]
        judge = FakeRunner({})

        cases, _ = score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

        assert cases[0].error == "no generated answer"

    def test_malformed_verdict_recorded_as_case_error(self) -> None:
        rows = [_retrieval_row("q1", "information_extraction", ["nugget"])]
        qa_cases = [_qa_case("q1", "some answer")]
        judge = FakeRunner({}, default="I refuse to emit JSON")

        cases, summaries = score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

        assert cases[0].error is not None
        assert "no JSON object" in cases[0].error
        # The failed call's tokens are still accounted for.
        assert cases[0].judge_calls == 1
        assert cases[0].judge_input_tokens == 10
        assert summaries[0].error_count == 1

    def test_unmatched_join_raises(self) -> None:
        rows = [_retrieval_row("q1", "information_extraction", ["nugget"])]
        qa_cases = [_qa_case("q-other", "answer")]
        judge = FakeRunner({})

        with pytest.raises(ValueError, match="No retrieval row"):
            score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)

    def test_row_without_rubric_metadata_raises(self) -> None:
        rows = [_retrieval_row("q1", "information_extraction", None)]
        qa_cases = [_qa_case("q1", "answer")]
        judge = FakeRunner({})

        with pytest.raises(ValueError, match="ability"):
            score_beam_cases(qa_cases, rows, judge=judge, max_workers=1)


class TestSummarizeBeamCases:
    def test_all_ten_abilities_always_present(self) -> None:
        cases = [_case_score("q1", "information_extraction", ability_score=1.0)]

        summary = summarize_beam_cases("bm-local", cases, "fake:test")

        assert set(summary.by_ability) == set(ABILITY_KEYS)
        assert summary.by_ability["temporal_reasoning"].question_count == 0

    def test_event_ordering_headline_uses_tau_norm(self) -> None:
        ordering = BeamOrderingScore(
            precision=1.0, recall=0.8, f1=0.8, tau_norm=0.5, final_score=0.4
        )
        cases = [
            _case_score(
                "q1", "event_ordering", ability_score=0.5, nugget_score=1.0, ordering=ordering
            )
        ]

        summary = summarize_beam_cases("bm-local", cases, "fake:test")
        ability = summary.by_ability["event_ordering"]

        assert ability.mean_score == 0.5  # tau_norm, not the nugget score
        assert ability.mean_nugget_score == 1.0
        assert ability.mean_f1 == 0.8

    def test_errored_cases_excluded_from_means_but_counted(self) -> None:
        cases = [
            _case_score("q1", "information_extraction", ability_score=1.0, nugget_score=1.0),
            _case_score("q2", "information_extraction", error="judge died"),
        ]

        summary = summarize_beam_cases("bm-local", cases, "fake:test")
        ability = summary.by_ability["information_extraction"]

        assert ability.question_count == 2
        assert ability.error_count == 1
        assert ability.mean_score == 1.0  # errored case excluded, not zero-scored
        # Token totals cover every case, errored included.
        assert ability.total_answer_input_tokens == 200
        assert ability.total_judge_input_tokens == 60
        assert summary.error_count == 1

    def test_macro_average_over_scored_abilities_only(self) -> None:
        cases = [
            _case_score("q1", "information_extraction", ability_score=1.0),
            _case_score("q2", "event_ordering", ability_score=0.5),
        ]

        summary = summarize_beam_cases("bm-local", cases, "fake:test")

        # Mean of the two scored ability means; the eight never-run abilities
        # do not deflate the average.
        assert summary.macro_average == pytest.approx(0.75)
        assert summary.total_cases == 2


class TestBeamScoreStage:
    def _write_rows(self, run_dir: Path, rows: list[PerQueryRetrievalResult]) -> None:
        with (run_dir / "per-query-retrieval.jsonl").open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row.model_dump(mode="json")) + "\n")

    def _write_qa(self, path: Path, cases: list[QACaseResult]) -> None:
        with path.open("w", encoding="utf-8") as file:
            for case in cases:
                file.write(json.dumps(case.model_dump(mode="json")) + "\n")

    def test_stage_writes_artifacts(self, tmp_path: Path, monkeypatch) -> None:
        from basic_memory_benchmarks import runner as runner_module

        rows = [
            _retrieval_row(
                "q1",
                "information_extraction",
                ["States March 29"],
                question="When is the dentist appointment?",
            )
        ]
        self._write_rows(tmp_path, rows)
        self._write_qa(tmp_path / "per-query-qa.jsonl", [_qa_case("q1", "March 29")])

        fake = FakeRunner({}, default='{"score": 1.0, "reason": "stated"}')
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: fake)

        out = runner_module.run_beam_score_stage(
            run_dir=tmp_path, judge_spec="fake:test", max_workers=1
        )

        assert out == tmp_path
        beam_lines = (tmp_path / "per-query-beam.jsonl").read_text().splitlines()
        scored = [BeamCaseScore.model_validate(json.loads(line)) for line in beam_lines]
        assert len(scored) == 1
        assert scored[0].ability_score == 1.0

        summary = json.loads((tmp_path / "beam-summary.json").read_text())
        assert summary["judge"] == "fake:test"
        assert summary["source"] == "per-query-qa.jsonl"
        provider_summary = summary["providers"][0]
        assert provider_summary["provider"] == "bm-local"
        assert set(provider_summary["by_ability"]) == set(ABILITY_KEYS)
        assert provider_summary["by_ability"]["information_extraction"]["mean_score"] == 1.0

        markdown = (tmp_path / "beam-summary.md").read_text()
        assert "## bm-local" in markdown
        assert "| information_extraction | 1 |" in markdown
        assert "Macro average" in markdown

    def test_non_beam_run_fails_fast(self, tmp_path: Path, monkeypatch) -> None:
        from basic_memory_benchmarks import runner as runner_module

        self._write_rows(tmp_path, [_retrieval_row("q1", "single_hop", None)])
        self._write_qa(tmp_path / "per-query-qa.jsonl", [_qa_case("q1", "answer")])

        fake = FakeRunner({})
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: fake)

        with pytest.raises(ValueError, match="does not look like a BEAM run"):
            runner_module.run_beam_score_stage(
                run_dir=tmp_path, judge_spec="fake:test", max_workers=1
            )
        assert not (tmp_path / "per-query-beam.jsonl").exists()

    def test_resume_judges_only_the_cases_the_last_pass_could_not(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The claude CLI judge hit its session limit after 130 of 400 raw cases;
        a re-run without resume would re-pay for those 130 and could hit the
        limit again before reaching the 270 that still needed a verdict."""
        from basic_memory_benchmarks import runner as runner_module
        from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner, LLMRunnerError

        self._write_rows(
            tmp_path,
            [
                _retrieval_row("q1", "information_extraction", ["States March 29"]),
                _retrieval_row("q2", "preference_following", ["Prefers tea"]),
            ],
        )
        self._write_qa(
            tmp_path / "per-query-qa.jsonl",
            [_qa_case("q1", "March 29"), _qa_case("q2", "tea, always")],
        )

        class LimitedJudge(LLMRunner):
            spec = "fake:test"

            def __init__(self) -> None:
                self.calls = 0
                self.failing = True

            def complete(self, prompt: str) -> LLMResult:
                self.calls += 1
                if self.failing and "tea" in prompt:
                    raise LLMRunnerError("session limit")
                return LLMResult(
                    text='{"score": 1.0, "reason": "stated"}',
                    model="fake",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=0.0,
                )

        judge = LimitedJudge()
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: judge)

        runner_module.run_beam_score_stage(run_dir=tmp_path, judge_spec="fake:test", max_workers=1)
        first = json.loads((tmp_path / "beam-summary.json").read_text())["providers"][0]
        assert first["error_count"] == 1
        calls_first = judge.calls

        judge.failing = False
        runner_module.run_beam_score_stage(
            run_dir=tmp_path, judge_spec="fake:test", max_workers=1, resume=True
        )
        assert judge.calls == calls_first + 1, "only the errored case was judged again"
        second = json.loads((tmp_path / "beam-summary.json").read_text())["providers"][0]
        assert second["error_count"] == 0
        rows = [
            BeamCaseScore.model_validate(json.loads(line))
            for line in (tmp_path / "per-query-beam.jsonl").read_text().splitlines()
        ]
        assert [row.query_id for row in rows] == ["q1", "q2"]
        assert all(row.error is None for row in rows)

        # A different judge re-scores everything: verdicts from two judges never mix.
        judge.spec = "fake:other"
        judge.calls = 0
        runner_module.run_beam_score_stage(
            run_dir=tmp_path, judge_spec="fake:other", max_workers=1, resume=True
        )
        assert judge.calls == 2

    def test_source_rejudge_selects_rejudged_answers(self, tmp_path: Path, monkeypatch) -> None:
        from basic_memory_benchmarks import runner as runner_module

        self._write_rows(
            tmp_path, [_retrieval_row("q1", "information_extraction", ["States March 29"])]
        )
        self._write_qa(tmp_path / "per-query-qa.jsonl", [_qa_case("q1", "original answer")])
        self._write_qa(tmp_path / "per-query-qa-rejudge.jsonl", [_qa_case("q1", "rejudged answer")])

        fake = FakeRunner({}, default='{"score": 0.5, "reason": "partial"}')
        monkeypatch.setattr("basic_memory_benchmarks.llm.runners.create_runner", lambda spec: fake)

        runner_module.run_beam_score_stage(
            run_dir=tmp_path, judge_spec="fake:test", source="rejudge", max_workers=1
        )

        summary = json.loads((tmp_path / "beam-summary.json").read_text())
        assert summary["source"] == "per-query-qa-rejudge.jsonl"
        beam_case = json.loads((tmp_path / "per-query-beam.jsonl").read_text().splitlines()[0])
        assert beam_case["generated_answer"] == "rejudged answer"


class TestBeamHeadlineSplit:
    def _plain_row(self, query_id: str, category: str, recall: float) -> PerQueryRetrievalResult:
        return PerQueryRetrievalResult(
            provider="bm-local",
            query_id=query_id,
            query_text="Q?",
            category=category,
            ground_truth=[],
            expected_answer=None,
            recall_at_5=recall,
            recall_at_10=recall,
            precision_at_5=0.0,
            mrr=0.0,
            content_hit=False,
            latency_ms=1.0,
        )

    def test_beam_dataset_puts_abstention_in_breakout(self) -> None:
        rows = [
            self._plain_row("q1", "information_extraction", 1.0),
            self._plain_row("q2", "temporal_reasoning", 1.0),
            self._plain_row("q3", "abstention", 0.0),
        ]

        summary = summarize_provider("bm-local", rows, dataset_id="beam-100k")

        assert summary.official_headline.query_count == 2
        assert summary.official_headline.recall_at_10 == 1.0
        assert summary.adversarial_breakout.query_count == 1

    def test_default_locomo_split_unchanged(self) -> None:
        rows = [
            self._plain_row("q1", "single_hop", 1.0),
            self._plain_row("q2", "adversarial", 0.0),
        ]

        summary = summarize_provider("bm-local", rows)

        assert summary.official_headline.query_count == 1
        assert summary.adversarial_breakout.query_count == 1

    def test_beam_categories_without_dataset_id_stay_out_of_headline(self) -> None:
        # Regression guard: the split is keyed on dataset_id, never guessed
        # from category names.
        rows = [self._plain_row("q1", "information_extraction", 1.0)]

        summary = summarize_provider("bm-local", rows)

        assert summary.official_headline.query_count == 0
        assert summary.adversarial_breakout.query_count == 0
