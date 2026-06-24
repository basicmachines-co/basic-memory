"""End-to-end QA scoring: answer generation over retrieved context, then judging.

This is the stage that produces comparable "memory benchmark accuracy" numbers.
Retrieval metrics (recall/MRR) measure the search layer; QA accuracy measures
whether an LLM holding only the retrieved memories can actually answer the
question. Both prompts below are fixed and ship with the repo so any published
number can be audited.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from basic_memory_benchmarks.llm.runners import LLMRunner, LLMRunnerError
from basic_memory_benchmarks.models import (
    PerQueryRetrievalResult,
    QACaseResult,
    QACategoryMetrics,
    QASummary,
    SearchHit,
)

# Context assembly budget. Multi-fact answers (LoCoMo multi_hop, LongMemEval
# multi-session) need material from several hits; the previous top-5
# matched-chunk join (~1K chars) capped every provider near zero on those
# categories despite ~0.8 retrieval recall.
CONTEXT_MAX_HITS = 10
CONTEXT_MAX_CHARS = 12_000
CONTEXT_CHARS_PER_HIT = 2_500

# The exact abstention sentinel the answer prompt requests. Judged correct only
# when the gold answer itself indicates the question is unanswerable (e.g.
# LoCoMo adversarial cases).
ABSTAIN_SENTINEL = "I don't know"

ANSWER_PROMPT_TEMPLATE = """\
You are answering a question using only the retrieved memories below. The memories
come from past conversations and notes; they may be incomplete.

Question: {question}

Retrieved memories:
{context}

Instructions:
- Answer concisely using only facts found in the retrieved memories.
- If the memories do not contain the information needed to answer, reply with
  exactly: {abstain}
- Do not use outside knowledge. Do not explain your reasoning.

Answer:"""

JUDGE_PROMPT_TEMPLATE = """\
You are grading a question-answering system against a reference (gold) answer.

Question: {question}
Gold answer: {gold}
Candidate answer: {candidate}

FIRST, handle the unanswerable case. If the gold answer indicates the
information is NOT available (e.g. "no information", "not mentioned", "cannot
be determined"), then judge ONLY on whether the candidate declines:
- correct = true if the candidate also declines / abstains (e.g. "{abstain}").
- correct = false if the candidate asserts a specific factual answer.
Do not apply the fact-matching rules below to this case.

OTHERWISE (the gold answer contains real facts), the gold answer may be
INCOMPLETE: it lists the key fact(s) the candidate must cover, but is not
necessarily an exhaustive list.

Mark correct = true when BOTH hold:
- The candidate states every key fact in the gold answer (paraphrase and
  formatting differences are fine).
- The candidate does not contradict the gold answer.
Additional specific facts in the candidate beyond the gold answer are NOT errors;
do not treat them as hallucination unless they directly contradict the gold.

Mark correct = false when ANY holds:
- A key fact from the gold answer is missing from the candidate.
- The candidate contradicts the gold answer.
- The candidate declines to answer (e.g. "{abstain}") even though the gold
  answer contains a real fact.

Reply with only a JSON object: {{"correct": true or false, "reason": "<one sentence>"}}"""

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def build_answer_prompt(question: str, context: str) -> str:
    return ANSWER_PROMPT_TEMPLATE.format(
        question=question,
        context=context if context.strip() else "(no memories were retrieved)",
        abstain=ABSTAIN_SENTINEL,
    )


def build_judge_prompt(question: str, gold: str, candidate: str) -> str:
    return JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        gold=gold,
        candidate=candidate,
        abstain=ABSTAIN_SENTINEL,
    )


def parse_judge_verdict(raw: str) -> tuple[bool, str]:
    """Extract a (correct, reason) verdict from judge output.

    Judges occasionally wrap JSON in prose or code fences; take the first JSON
    object found. A malformed verdict raises so the case is recorded as an
    explicit error rather than silently scored.
    """
    match = _JSON_OBJECT_PATTERN.search(raw)
    if not match:
        raise ValueError(f"Judge returned no JSON object: {raw[:200]}")
    payload = json.loads(match.group(0))
    if not isinstance(payload.get("correct"), bool):
        raise ValueError(f"Judge JSON missing boolean 'correct': {raw[:200]}")
    return payload["correct"], str(payload.get("reason") or "")


def _is_abstention(answer: str) -> bool:
    normalized = answer.strip().strip(".").lower()
    return normalized == ABSTAIN_SENTINEL.strip(".").lower()


def assemble_context(hits: list[SearchHit], max_chars: int = CONTEXT_MAX_CHARS) -> str:
    """Build answering context from ranked hits under a character budget.

    Hits are taken in rank order under a total budget of ``max_chars``. The
    per-hit cap is the larger of CONTEXT_CHARS_PER_HIT and an even split of
    the budget across available hits, so a single-hit provider (the
    full-context baseline) can use the whole budget rather than being
    truncated to one hit-slice. Sections are numbered with their source doc
    so the answerer can ground multi-fact answers across memories. Identical
    assembly for every provider in a run.
    """
    take = hits[:CONTEXT_MAX_HITS]
    if not take:
        return ""
    per_hit_cap = max(CONTEXT_CHARS_PER_HIT, max_chars // len(take))
    sections: list[str] = []
    used = 0
    for rank, hit in enumerate(take, start=1):
        text = (hit.text or "").strip()
        if not text:
            continue
        snippet = text[:per_hit_cap]
        if used + len(snippet) > max_chars:
            snippet = snippet[: max_chars - used]
            if not snippet:
                break
        source = hit.source_doc_id or hit.source_path or "unknown"
        title = (hit.metadata or {}).get("title")
        header = f"[Memory {rank} | source: {source}]"
        if title and str(title) not in snippet:
            header = f"[Memory {rank} | source: {source} | {title}]"
        sections.append(f"{header}\n{snippet}")
        used += len(snippet)
        if used >= max_chars:
            break
    return "\n\n".join(sections)


def _row_context(row: PerQueryRetrievalResult, max_context_chars: int) -> str:
    # Prefer assembling from stored hits (richer, budget-controlled); fall
    # back to the legacy pre-joined context for old artifacts without hits.
    if row.hits:
        assembled = assemble_context(row.hits, max_chars=max_context_chars)
        if assembled:
            return assembled
    return row.retrieved_context


def _question_display(row: PerQueryRetrievalResult) -> str:
    """Render the question with its ask-date when the dataset provides one.

    Temporal-reasoning questions ("how many weeks ago...") are unanswerable
    without the reference date, and both the answerer and the judge need the
    same framing.
    """
    question_date = row.metadata.get("question_date")
    if question_date:
        return f"{row.query_text} (question asked on {question_date})"
    return row.query_text


def _score_case(
    row: PerQueryRetrievalResult,
    provider: str,
    answerer: LLMRunner,
    judge: LLMRunner,
    max_context_chars: int = CONTEXT_MAX_CHARS,
) -> QACaseResult:
    question = _question_display(row)
    answer_prompt = build_answer_prompt(question, _row_context(row, max_context_chars))
    try:
        answer_result = answerer.complete(answer_prompt)
        judge_result = judge.complete(
            build_judge_prompt(question, row.expected_answer or "", answer_result.text)
        )
        correct, reason = parse_judge_verdict(judge_result.text)
        return QACaseResult(
            provider=provider,
            query_id=row.query_id,
            category=row.category,
            question=row.query_text,
            expected_answer=row.expected_answer or "",
            generated_answer=answer_result.text,
            abstained=_is_abstention(answer_result.text),
            correct=correct,
            judge_reason=reason,
            answer_model=answerer.spec,
            judge_model=judge.spec,
            answer_latency_ms=answer_result.latency_ms,
            answer_input_tokens=answer_result.input_tokens,
            answer_output_tokens=answer_result.output_tokens,
            answer_prompt_chars=len(answer_prompt),
        )
    except (LLMRunnerError, ValueError, json.JSONDecodeError) as exc:
        return QACaseResult(
            provider=provider,
            query_id=row.query_id,
            category=row.category,
            question=row.query_text,
            expected_answer=row.expected_answer or "",
            generated_answer="",
            abstained=False,
            correct=False,
            judge_reason="",
            answer_model=answerer.spec,
            judge_model=judge.spec,
            answer_latency_ms=0.0,
            answer_input_tokens=0,
            answer_output_tokens=0,
            answer_prompt_chars=len(answer_prompt),
            error=str(exc),
        )


def _rejudge_case(case: QACaseResult, judge: LLMRunner) -> QACaseResult:
    """Re-judge one stored case against its generated answer (no regeneration).

    Errored cases (no generated answer) are returned unchanged. The answerer
    fields are preserved; only the verdict and judge_model are updated.
    """
    if case.error:
        return case
    try:
        verdict = judge.complete(
            build_judge_prompt(case.question, case.expected_answer, case.generated_answer)
        )
        correct, reason = parse_judge_verdict(verdict.text)
    except (LLMRunnerError, ValueError, json.JSONDecodeError) as exc:
        return case.model_copy(update={"error": f"rejudge failed: {exc}"})
    return case.model_copy(
        update={"correct": correct, "judge_reason": reason, "judge_model": judge.spec}
    )


def rejudge_cases(
    cases: list[QACaseResult],
    *,
    judge: LLMRunner,
    max_workers: int = 4,
) -> tuple[list[QACaseResult], QASummary, list[dict]]:
    """Re-judge stored QA cases with a (possibly different) judge.

    Returns the re-judged cases, a summary, and the list of verdict flips
    (cases whose correctness changed) for calibration review.
    """
    if not cases:
        return (
            [],
            QASummary(
                provider="rejudge",
                answer_model="stored",
                judge_model=judge.spec,
                total_cases=0,
                correct_count=0,
                error_count=0,
                abstain_count=0,
                accuracy=0.0,
                skipped_reason="No stored cases to re-judge",
            ),
            [],
        )

    provider = cases[0].provider
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        rejudged = list(pool.map(lambda c: _rejudge_case(c, judge), cases))

    flips = [
        {
            "query_id": new.query_id,
            "category": new.category,
            "question": new.question,
            "expected_answer": new.expected_answer,
            "generated_answer": new.generated_answer,
            "was_correct": old.correct,
            "now_correct": new.correct,
            "old_reason": old.judge_reason,
            "new_reason": new.judge_reason,
        }
        for old, new in zip(cases, rejudged)
        if old.correct != new.correct
    ]
    summary = _summarize_cases(rejudged, provider=provider, answer_model="stored", judge=judge)
    return rejudged, summary, flips


def _summarize_cases(
    case_results: list[QACaseResult],
    *,
    provider: str,
    answer_model: str,
    judge: LLMRunner,
) -> QASummary:
    by_category: dict[str, QACategoryMetrics] = {}
    for case in case_results:
        bucket = by_category.setdefault(case.category, QACategoryMetrics())
        bucket.total += 1
        bucket.correct += 1 if case.correct else 0
    for bucket in by_category.values():
        bucket.accuracy = bucket.correct / bucket.total if bucket.total else 0.0
    correct_count = sum(1 for case in case_results if case.correct)
    return QASummary(
        provider=provider,
        answer_model=answer_model,
        judge_model=judge.spec,
        total_cases=len(case_results),
        correct_count=correct_count,
        error_count=sum(1 for case in case_results if case.error),
        abstain_count=sum(1 for case in case_results if case.abstained),
        accuracy=correct_count / len(case_results) if case_results else 0.0,
        by_category=by_category,
    )


def run_qa(
    rows: list[PerQueryRetrievalResult],
    *,
    provider: str,
    answerer: LLMRunner,
    judge: LLMRunner,
    max_workers: int = 4,
    max_context_chars: int = CONTEXT_MAX_CHARS,
) -> tuple[list[QACaseResult], QASummary]:
    """Answer and judge every row that carries an expected answer."""
    scorable = [row for row in rows if row.expected_answer]
    if not scorable:
        return [], QASummary(
            provider=provider,
            answer_model=answerer.spec,
            judge_model=judge.spec,
            total_cases=0,
            correct_count=0,
            error_count=0,
            abstain_count=0,
            accuracy=0.0,
            skipped_reason="No expected answers available for QA scoring",
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        case_results = list(
            pool.map(
                lambda row: _score_case(row, provider, answerer, judge, max_context_chars),
                scorable,
            )
        )

    by_category: dict[str, QACategoryMetrics] = {}
    for case in case_results:
        bucket = by_category.setdefault(case.category, QACategoryMetrics())
        bucket.total += 1
        bucket.correct += 1 if case.correct else 0
    for bucket in by_category.values():
        bucket.accuracy = bucket.correct / bucket.total if bucket.total else 0.0

    correct_count = sum(1 for case in case_results if case.correct)
    error_count = sum(1 for case in case_results if case.error)
    summary = QASummary(
        provider=provider,
        answer_model=answerer.spec,
        judge_model=judge.spec,
        total_cases=len(case_results),
        correct_count=correct_count,
        error_count=error_count,
        abstain_count=sum(1 for case in case_results if case.abstained),
        accuracy=correct_count / len(case_results),
        by_category=by_category,
        mean_answer_latency_ms=(
            sum(case.answer_latency_ms for case in case_results) / len(case_results)
        ),
        total_answer_input_tokens=sum(case.answer_input_tokens for case in case_results),
        total_answer_output_tokens=sum(case.answer_output_tokens for case in case_results),
        mean_answer_prompt_chars=(
            sum(case.answer_prompt_chars for case in case_results) / len(case_results)
        ),
    )
    return case_results, summary
