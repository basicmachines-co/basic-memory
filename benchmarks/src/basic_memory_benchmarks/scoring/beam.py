"""BEAM nugget-methodology scoring over stored QA answers.

Runs as a post-hoc stage (like rejudge/diagnose): it joins the stored QA
answers (``per-query-qa.jsonl``) with the retrieval rows carrying each probe's
rubric (``per-query-retrieval.jsonl``) on ``(provider, query_id)``, then
applies BEAM's scoring:

- every reference answer is pre-decomposed upstream into atomic nuggets (the
  probe's ``rubric``); an LLM judge scores each nugget 0/0.5/1 against the
  generated answer, and the per-question nugget score is the mean;
- Event Ordering additionally aligns the answer's lines against the ordered
  reference events with an LLM equivalence judge and scores Kendall tau-b over
  the union rank vectors (``tau_norm = (tau_b + 1) / 2``);
- the per-ability headline is the mean of ``tau_norm`` for event_ordering and
  of the nugget score for the other nine abilities, mirroring upstream
  ``src/evaluation/report_results.py`` (which aggregates ``tau_norm``, not
  ``final_score``).

Answer generation itself stays the untouched ``run qa`` stage — same fixed
answer prompt, same context budget, same abstention sentinel for every
provider (the fairness contract). Its binary verdict remains as a coarse
cross-check and keeps review/diagnose/rejudge tooling functional on BEAM runs.

Provenance: prompts and math are adapted from mohammadtavakoli78/BEAM
(MIT-licensed code; ICLR 2026, arXiv 2510.27246) — see the per-constant and
per-function comments for exact upstream locations and deliberate deviations.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import mean

from basic_memory_benchmarks.datasets.beam import ABILITY_KEYS
from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner, LLMRunnerError
from basic_memory_benchmarks.models import (
    BeamAbilitySummary,
    BeamCaseScore,
    BeamNuggetVerdict,
    BeamOrderingScore,
    BeamSummary,
    PerQueryRetrievalResult,
    QACaseResult,
)
from basic_memory_benchmarks.scoring.qa import question_display

# Adapted from ``unified_llm_judge_base_prompt`` in BEAM ``src/prompts.py``
# (line 11547 at the adapted revision). Kept verbatim: positive/negative
# constraint handling, semantic tolerance, style neutrality, the 1.0/0.5/0.0
# scale, and JSON output. Deliberate modifications:
# 1. upstream's ``<rubric_item>``/``<llm_response>`` placeholders become
#    ``{rubric_item}``/``{response}`` .format slots (JSON braces escaped);
# 2. a QUESTION input line is added — upstream's RESPONSIVENESS section scores
#    against "the QUESTION" but never injects it (an upstream bug); we supply
#    it via the same question_display() rendering the answerer saw.
BEAM_NUGGET_JUDGE_PROMPT = """\
You are an expert evaluator tasked with judging whether the LLM's response demonstrates compliance with the specified RUBRIC CRITERION.

## EVALUATION INPUTS
- QUESTION (what the response was asked): {question}
- RUBRIC CRITERION (what to check): {rubric_item}
- RESPONSE TO EVALUATE: {response}

## EVALUATION RUBRIC:
The rubric defines a specific requirement, constraint, or expected behavior that the LLM response should demonstrate.

**IMPORTANT**: Pay careful attention to whether the rubric specifies:
- **Positive requirements** (things the response SHOULD include/do)
- **Negative constraints** (things the response SHOULD NOT include/do, often indicated by "no", "not", "avoid", "absent")

## RESPONSIVENESS REQUIREMENT
A compliant response must be **on-topic** and attempt to answer it.
- If the response does not address the QUESTION, score **0.0** and stop.
- For negative constraints, both must hold: (a) the response is responsive to the QUESTION, and (b) the prohibited element is absent.

## SEMANTIC TOLERANCE RULES:
Judge by meaning, not exact wording.
- Accept **paraphrases** and **synonyms** that preserve intent.
- **Case/punctuation/whitespace** differences must be ignored.
- **Numbers/currencies/dates** may appear in equivalent forms (e.g., "$68,000", "68k", "68,000 USD", or "sixty-eight thousand dollars"). Treat them as equal when numerically equivalent.
- If the rubric expects a number or duration, prefer **normalized comparison** (extract and compare values) over string matching.

## STYLE NEUTRALITY (prevents style contamination):
Ignore tone, politeness, length, and flourish unless the rubric explicitly requires a format/structure (e.g., "itemized list", "no citations", "one sentence").
- Do **not** penalize hedging, voice, or verbosity if content satisfies the rubric.
- Only evaluate format when the rubric **explicitly** mandates it.

## SCORING SCALE:
- **1.0 (Complete Compliance)**: Fully complies with the rubric criterion.
  - Positive: required element present, accurate, properly executed (allowing semantic equivalents).
  - Negative: prohibited element **absent** AND response is **responsive**.

- **0.5 (Partial Compliance)**: Partially complies.
  - Positive: element present but minor inaccuracies/incomplete execution.
  - Negative: generally responsive and mostly avoids the prohibited element but with minor/edge violations.

- **0.0 (No Compliance)**: Fails to comply.
  - Positive: required element missing or incorrect.
  - Negative: prohibited element present **or** response is non-responsive/evasive even if the element is absent.

## EVALUATION INSTRUCTIONS:
1. **Understand the Requirement**: Determine if the rubric is asking for something to be present (positive) or absent (negative/constraint).

2. **Parse Compound Statements**: If the rubric contains multiple elements connected by "and" or commas, evaluate whether:
   - **All elements** must be present for full compliance (1.0)
   - **Some elements** present indicates partial compliance (0.5)
   - **No elements** present indicates no compliance (0.0)

3. **Check Compliance**:
   - For positive requirements: Look for the presence and quality of the required element
   - For negative constraints: Look for the absence of the prohibited element

4. **Assign Score**: Based on compliance with the specific rubric criterion according to the scoring scale above.

5. **Provide Reasoning**: Explain whether the rubric criterion was satisfied and justify the score.

## OUTPUT FORMAT:
Return your evaluation in JSON format with two fields:

{{
   "score": [your score: 1.0, 0.5, or 0.0],
   "reason": "[detailed explanation of whether the rubric criterion was satisfied and why this justified the assigned score]"
}}

NOTE: ONLY output the json object, without any explanation before or after that"""

# Adapted from ``llm_equivalence`` in BEAM ``src/evaluation/compute_metrics.py``:
# upstream sends a system+user message pair; our LLMRunner.complete is
# single-prompt, so the two are merged into one prompt. The classifier text is
# otherwise verbatim (including upstream's "exaplanation" typo) and the reply
# is parsed upstream-style: "yes" in response.lower().
BEAM_EQUIVALENCE_PROMPT = """\
You are a binary classifier.
If the TWO snippets describe the SAME event/fact, reply **YES**
Otherwise reply **NO**. No extra words.
DO NOT provide any exaplanation.

First snippet: {first}

Second snippet: {second}"""

_ALLOWED_NUGGET_SCORES = (0.0, 0.5, 1.0)

# Same first-JSON-object extraction the QA judge parser uses.
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class JudgeUsage:
    """Accumulates judge-side token usage across a case's calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, result: LLMResult) -> None:
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.calls += 1


def parse_nugget_verdict(raw: str) -> tuple[float, str]:
    """Extract a (score, reason) nugget verdict from judge output.

    Like qa.parse_judge_verdict, takes the first JSON object found. The score
    must be exactly one of {0.0, 0.5, 1.0}; anything else raises so the case
    is recorded as an explicit error. Deviation from upstream: no json_repair
    dependency — silent repair conflicts with the package's fail-fast rule, so
    malformed verdicts become explicit per-case errors instead.
    """
    match = _JSON_OBJECT_PATTERN.search(raw)
    if not match:
        raise ValueError(f"Nugget judge returned no JSON object: {raw[:200]}")
    payload = json.loads(match.group(0))
    score = payload.get("score")
    if not isinstance(score, (int, float)) or float(score) not in _ALLOWED_NUGGET_SCORES:
        raise ValueError(f"Nugget judge score must be 0, 0.5 or 1: {raw[:200]}")
    return float(score), str(payload.get("reason") or "")


def score_nuggets(
    question: str,
    rubric: list[str],
    response: str,
    judge: LLMRunner,
    usage: JudgeUsage,
) -> list[BeamNuggetVerdict]:
    """Judge each rubric item against the response (one call per nugget).

    Serial within a case, mirroring the upstream loop; parallelism lives at
    the case level so per-case token accounting stays simple.
    """
    verdicts: list[BeamNuggetVerdict] = []
    for item in rubric:
        result = judge.complete(
            BEAM_NUGGET_JUDGE_PROMPT.format(question=question, rubric_item=item, response=response)
        )
        usage.add(result)
        score, reason = parse_nugget_verdict(result.text)
        verdicts.append(BeamNuggetVerdict(nugget=item, score=score, reason=reason))
    return verdicts


def kendall_tau_b(x: Sequence[int], y: Sequence[int]) -> float | None:
    """Kendall tau-b with tie correction, pure Python.

    Replaces upstream's scipy.stats.kendalltau(variant="b") — the rank vectors
    here have <= ~10 entries, so the O(n^2) pair count is trivial and scipy is
    not worth a dependency. Returns None when a tie-corrected denominator is
    zero (scipy returns nan there).
    """
    if len(x) != len(y):
        raise ValueError(f"Rank vectors differ in length: {len(x)} != {len(y)}")
    n = len(x)
    concordant = discordant = 0
    ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            if dx == 0:
                ties_x += 1
            if dy == 0:
                ties_y += 1
            if dx * dy > 0:
                concordant += 1
            elif dx * dy < 0:
                discordant += 1
    total_pairs = n * (n - 1) // 2
    denominator = math.sqrt((total_pairs - ties_x) * (total_pairs - ties_y))
    if denominator == 0:
        return None
    return (concordant - discordant) / denominator


def _align_with_judge(
    reference: list[str], system: list[str], judge: LLMRunner, usage: JudgeUsage
) -> list[str]:
    """Replicate BEAM ``align_with_llm``: greedy first-match canonicalisation.

    Each system line is matched against the first unused reference item the
    equivalence judge accepts; matched lines are REPLACED by the reference
    string so downstream set/rank math operates on canonical labels.
    """
    used: set[int] = set()
    system_out: list[str] = []
    for system_item in system:
        matched_index: int | None = None
        for index, reference_item in enumerate(reference):
            if index in used:
                continue
            result = judge.complete(
                BEAM_EQUIVALENCE_PROMPT.format(first=reference_item, second=system_item)
            )
            usage.add(result)
            if "yes" in result.text.lower():
                matched_index = index
                break
        if matched_index is not None:
            system_out.append(reference[matched_index])
            used.add(matched_index)
        else:
            system_out.append(system_item)
    return system_out


def event_ordering_score(
    reference: list[str], system: list[str], judge: LLMRunner, usage: JudgeUsage
) -> BeamOrderingScore:
    """Replicate BEAM ``event_ordering_score`` with align_type="llm".

    Set precision/recall/F1 over canonicalised labels, then Kendall tau-b over
    union rank vectors (missing items share the tie rank), normalised to
    ``tau_norm = (tau_b + 1) / 2`` with ``final_score = tau_norm * f1`` —
    the math mirrors upstream src/evaluation/compute_metrics.py exactly.
    """
    system_canon = _align_with_judge(reference, system, judge, usage)

    reference_set = set(reference)
    canon_set = set(system_canon)
    true_positive = len(reference_set & canon_set)
    false_positive = sum(1 for item in system_canon if item not in reference_set)
    false_negative = sum(1 for item in reference if item not in canon_set)

    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    union = list(dict.fromkeys(reference + system_canon))
    tie_rank = len(union) + 1

    def to_rank(sequence: list[str]) -> list[int]:
        positions = {item: position + 1 for position, item in enumerate(sequence)}
        return [positions.get(item, tie_rank) for item in union]

    tau = kendall_tau_b(to_rank(reference), to_rank(system_canon))
    tau_norm = (tau + 1) / 2 if tau is not None else 0.0

    return BeamOrderingScore(
        precision=precision,
        recall=recall,
        f1=f1,
        tau_norm=tau_norm,
        final_score=tau_norm * f1,
    )


def _errored_case(
    qa_case: QACaseResult, ability: str, question: str, judge_model: str, error: str
) -> BeamCaseScore:
    # Explicit-failure principle: errored cases are never silently
    # zero-scored — they carry the error and are excluded from means.
    return BeamCaseScore(
        provider=qa_case.provider,
        query_id=qa_case.query_id,
        ability=ability,
        question=question,
        generated_answer=qa_case.generated_answer,
        answer_input_tokens=qa_case.answer_input_tokens,
        answer_output_tokens=qa_case.answer_output_tokens,
        answer_prompt_chars=qa_case.answer_prompt_chars,
        answer_latency_ms=qa_case.answer_latency_ms,
        judge_model=judge_model,
        error=error,
    )


def _score_case(
    qa_case: QACaseResult,
    retrieval_row: PerQueryRetrievalResult,
    judge: LLMRunner,
) -> BeamCaseScore:
    ability = retrieval_row.metadata.get("ability")
    if not isinstance(ability, str) or not ability:
        raise ValueError(f"Retrieval row {qa_case.query_id} carries no 'ability' metadata")
    rubric = retrieval_row.metadata.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        raise ValueError(f"Retrieval row {qa_case.query_id} carries no 'rubric' metadata")

    # Fairness guarantee: the judge sees the identical question rendering the
    # answerer saw (including any ask-date framing).
    question = question_display(retrieval_row)

    if qa_case.error or not qa_case.generated_answer:
        return _errored_case(
            qa_case, ability, question, judge.spec, qa_case.error or "no generated answer"
        )

    usage = JudgeUsage()
    try:
        verdicts = score_nuggets(
            question, [str(item) for item in rubric], qa_case.generated_answer, judge, usage
        )
        nugget_score = mean(verdict.score for verdict in verdicts)

        ordering: BeamOrderingScore | None = None
        if ability == "event_ordering":
            # Upstream splits the raw response on newlines; dropping blank
            # lines is a minimal commented cleanup (blank lines carry no
            # event and would only inflate false positives).
            system_lines = [line for line in qa_case.generated_answer.split("\n") if line.strip()]
            ordering = event_ordering_score(
                [str(item) for item in rubric], system_lines, judge, usage
            )

        # Aggregation rule (upstream report_results.py): event_ordering
        # headlines on tau_norm; every other ability on the nugget score.
        ability_score = ordering.tau_norm if ordering is not None else nugget_score

        return BeamCaseScore(
            provider=qa_case.provider,
            query_id=qa_case.query_id,
            ability=ability,
            question=question,
            generated_answer=qa_case.generated_answer,
            nugget_verdicts=verdicts,
            nugget_score=nugget_score,
            ordering=ordering,
            ability_score=ability_score,
            answer_input_tokens=qa_case.answer_input_tokens,
            answer_output_tokens=qa_case.answer_output_tokens,
            answer_prompt_chars=qa_case.answer_prompt_chars,
            answer_latency_ms=qa_case.answer_latency_ms,
            judge_input_tokens=usage.input_tokens,
            judge_output_tokens=usage.output_tokens,
            judge_calls=usage.calls,
            judge_model=judge.spec,
        )
    except (LLMRunnerError, ValueError, json.JSONDecodeError) as exc:
        errored = _errored_case(qa_case, ability, question, judge.spec, str(exc))
        return errored.model_copy(
            update={
                "judge_input_tokens": usage.input_tokens,
                "judge_output_tokens": usage.output_tokens,
                "judge_calls": usage.calls,
            }
        )


def _summarize_ability(ability: str, cases: list[BeamCaseScore]) -> BeamAbilitySummary:
    scored = [case for case in cases if case.error is None]
    ordering_scores = [case.ordering for case in scored if case.ordering is not None]
    return BeamAbilitySummary(
        ability=ability,
        question_count=len(cases),
        error_count=len(cases) - len(scored),
        mean_score=mean(case.ability_score for case in scored) if scored else 0.0,
        mean_nugget_score=mean(case.nugget_score for case in scored) if scored else 0.0,
        mean_f1=mean(item.f1 for item in ordering_scores) if ordering_scores else None,
        # Token totals cover every case (errored calls still cost tokens).
        total_answer_input_tokens=sum(case.answer_input_tokens for case in cases),
        total_answer_output_tokens=sum(case.answer_output_tokens for case in cases),
        total_judge_input_tokens=sum(case.judge_input_tokens for case in cases),
        total_judge_output_tokens=sum(case.judge_output_tokens for case in cases),
        mean_answer_prompt_chars=(
            mean(case.answer_prompt_chars for case in cases) if cases else 0.0
        ),
    )


def summarize_beam_cases(
    provider: str, cases: list[BeamCaseScore], judge_model: str
) -> BeamSummary:
    by_ability_cases: dict[str, list[BeamCaseScore]] = {ability: [] for ability in ABILITY_KEYS}
    for case in cases:
        by_ability_cases.setdefault(case.ability, []).append(case)

    by_ability = {
        ability: _summarize_ability(ability, ability_cases)
        for ability, ability_cases in by_ability_cases.items()
    }
    # Macro average over abilities that actually produced scores: including
    # never-run abilities as 0.0 would silently deflate partial runs (a
    # deviation from upstream, which always scores all ten).
    scored_means = [
        summary.mean_score
        for summary in by_ability.values()
        if summary.question_count - summary.error_count > 0
    ]
    return BeamSummary(
        provider=provider,
        judge_model=judge_model,
        total_cases=len(cases),
        error_count=sum(1 for case in cases if case.error is not None),
        by_ability=by_ability,
        macro_average=mean(scored_means) if scored_means else 0.0,
    )


def score_beam_cases(
    qa_cases: list[QACaseResult],
    retrieval_rows: list[PerQueryRetrievalResult],
    *,
    judge: LLMRunner,
    max_workers: int = 4,
) -> tuple[list[BeamCaseScore], list[BeamSummary]]:
    """Join QA answers with their rubric-bearing retrieval rows and score them.

    An unmatched join raises: a BEAM run always writes both artifacts, so a
    missing row means the run dir is inconsistent, not a scoreable state.
    """
    rows_by_key = {(row.provider, row.query_id): row for row in retrieval_rows}
    joined: list[tuple[QACaseResult, PerQueryRetrievalResult]] = []
    for qa_case in qa_cases:
        row = rows_by_key.get((qa_case.provider, qa_case.query_id))
        if row is None:
            raise ValueError(
                f"No retrieval row for QA case ({qa_case.provider}, {qa_case.query_id})"
            )
        joined.append((qa_case, row))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        case_scores = list(pool.map(lambda pair: _score_case(pair[0], pair[1], judge), joined))

    providers: list[str] = []
    cases_by_provider: dict[str, list[BeamCaseScore]] = {}
    for case in case_scores:
        if case.provider not in cases_by_provider:
            providers.append(case.provider)
            cases_by_provider[case.provider] = []
        cases_by_provider[case.provider].append(case)

    summaries = [
        summarize_beam_cases(provider, cases_by_provider[provider], judge.spec)
        for provider in providers
    ]
    return case_scores, summaries


def build_beam_summary_markdown(summaries: list[BeamSummary], run_id: str, source: str) -> str:
    """Render per-provider, per-ability tables — never just an overall average."""
    lines: list[str] = [f"# BEAM Scoring — run `{run_id}`", ""]
    lines.append(f"- Source answers: `{source}`")
    if summaries:
        lines.append(f"- Judge: `{summaries[0].judge_model}`")
    lines.append("")

    for summary in summaries:
        lines.append(f"## {summary.provider}")
        lines.append("")
        lines.append(
            "| Ability | Questions | Errors | Score | Nugget | F1 | "
            "Answer tokens (in/out) | Judge tokens (in/out) |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for ability, ability_summary in summary.by_ability.items():
            f1_text = (
                f"{ability_summary.mean_f1:.3f}" if ability_summary.mean_f1 is not None else ""
            )
            lines.append(
                f"| {ability} | {ability_summary.question_count} | "
                f"{ability_summary.error_count} | {ability_summary.mean_score:.3f} | "
                f"{ability_summary.mean_nugget_score:.3f} | {f1_text} | "
                f"{ability_summary.total_answer_input_tokens}/"
                f"{ability_summary.total_answer_output_tokens} | "
                f"{ability_summary.total_judge_input_tokens}/"
                f"{ability_summary.total_judge_output_tokens} |"
            )
        lines.append(
            f"| **Macro average** | {summary.total_cases} | {summary.error_count} | "
            f"{summary.macro_average:.3f} |  |  |  |  |"
        )
        lines.append("")

    return "\n".join(lines).strip() + "\n"
