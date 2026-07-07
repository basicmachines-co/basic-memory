"""Core benchmark models and artifact schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


PROVIDER_STATE = Literal["ok", "skipped", "error"]


class DatasetProvenance(BaseModel):
    dataset_id: str
    source_url: str
    checksum_sha256: str
    license_note: str
    fetched_at_utc: str


class QueryCase(BaseModel):
    id: str
    query: str
    category: str
    category_id: int | None = None
    # Grouped datasets (LongMemEval) scope each query to its own corpus group.
    group: str | None = None
    ground_truth: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    id: str | None = None
    source_doc_id: str | None = None
    source_path: str | None = None
    text: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalMetrics(BaseModel):
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    precision_at_5: float = 0.0
    mrr: float = 0.0
    content_hit_rate: float = 0.0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    query_count: int = 0


class PerQueryRetrievalResult(BaseModel):
    provider: str
    query_id: str
    query_text: str
    category: str
    category_id: int | None = None
    ground_truth: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    hits: list[SearchHit] = Field(default_factory=list)
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    mrr: float
    content_hit: bool
    latency_ms: float
    top_hit_doc_id: str | None = None
    retrieved_context: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalSummary(BaseModel):
    provider: str
    metrics: RetrievalMetrics
    by_category: dict[str, RetrievalMetrics] = Field(default_factory=dict)
    official_headline: RetrievalMetrics
    adversarial_breakout: RetrievalMetrics


class JudgeCaseResult(BaseModel):
    provider: str
    query_id: str
    category: str
    passed: bool
    score: float
    reason: str
    evaluator: str


class JudgeSummary(BaseModel):
    provider: str
    evaluator: str
    model: str
    total_cases: int
    pass_count: int
    accuracy: float
    skipped_reason: str | None = None


class QACategoryMetrics(BaseModel):
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0


class QACaseResult(BaseModel):
    provider: str
    query_id: str
    category: str
    question: str
    expected_answer: str
    generated_answer: str
    abstained: bool
    correct: bool
    judge_reason: str
    answer_model: str
    judge_model: str
    answer_latency_ms: float
    answer_input_tokens: int
    answer_output_tokens: int
    # Size of the assembled prompt actually sent to the answerer. Runner
    # token accounting is transport-dependent (the claude CLI buries the
    # prompt in cache-creation alongside its own system overhead), so chars
    # are the comparable cross-provider context-size measure.
    answer_prompt_chars: int = 0
    error: str | None = None


class QASummary(BaseModel):
    provider: str
    answer_model: str
    judge_model: str
    total_cases: int
    correct_count: int
    error_count: int = 0
    abstain_count: int = 0
    accuracy: float
    by_category: dict[str, QACategoryMetrics] = Field(default_factory=dict)
    mean_answer_latency_ms: float = 0.0
    total_answer_input_tokens: int = 0
    total_answer_output_tokens: int = 0
    mean_answer_prompt_chars: float = 0.0
    skipped_reason: str | None = None


class CategoryDiagnosis(BaseModel):
    """Per-category answerer-vs-retrieval attribution of QA failures."""

    answerable: int = 0
    correct: int = 0
    retrieved_but_unused: int = 0  # gold retrieved, answer still wrong → answerer's fault
    truly_missed: int = 0  # gold not retrieved → retrieval's fault


class ProviderDiagnosis(BaseModel):
    """Attributes each provider's QA outcomes to retrieval vs the answerer.

    For every answerable question (non-empty ``ground_truth``) we join the QA
    verdict with the retrieval row on ``(provider, query_id)``. A wrong answer
    where the gold doc WAS retrieved (recall > 0) is an answerer failure
    ("retrieved but unused"); a wrong answer where it was NOT retrieved is a
    genuine retrieval miss. This separates "BM didn't find it" from "the fixed
    answerer couldn't use what BM found" — the absolute QA number conflates the
    two, and the answerer is held constant across providers.
    """

    provider: str
    total_cases: int = 0
    answerable: int = 0
    unanswerable: int = 0  # empty ground_truth (abstention items) — no retrieval attribution
    errored: int = 0  # QA-stage error (answerer/judge crashed)
    unmatched: int = 0  # no retrieval row to join against
    correct: int = 0
    retrieved_but_unused: int = 0
    truly_missed: int = 0
    # Derived shares over answerable questions (0.0 when answerable == 0):
    qa_accuracy: float = 0.0  # correct / answerable
    retrieval_ceiling: float = (
        0.0  # (correct + retrieved_but_unused) / answerable — max QA if answerer were perfect
    )
    answerer_gap: float = (
        0.0  # retrieved_but_unused / answerable — headroom left on the table by the answerer
    )
    retrieval_gap: float = 0.0  # truly_missed / answerable — headroom that needs better retrieval
    answerer_failure_share: float = 0.0  # retrieved_but_unused / (answerable failures) — of what we got wrong, how much was the answerer
    recall_field: str = "recall_at_10"
    by_category: dict[str, CategoryDiagnosis] = Field(default_factory=dict)


class ProviderStatus(BaseModel):
    provider: str
    state: PROVIDER_STATE
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeInfo(BaseModel):
    os: str
    python_version: str
    started_at_utc: str


class RunConfig(BaseModel):
    run_id: str
    dataset_id: str
    dataset_path: str
    corpus_dir: str
    queries_path: str
    output_root: str = "benchmarks/runs"
    providers: list[str] = Field(default_factory=list)
    top_k: int = 10
    bm_source: str = "github:basicmachines-co/basic-memory@main"
    bm_local_path: str | None = None
    judge_enabled: bool = False
    judge_model: str = "gpt-4o-mini"
    allow_provider_skip: bool = True


class RunManifest(BaseModel):
    run_id: str
    created_at_utc: str
    benchmark_git_sha: str
    bm_source: str
    bm_resolved_sha: str | None = None
    bm_local_path: str | None = None
    mem0_version: str | None = None
    provider_versions: dict[str, dict[str, str]] = Field(default_factory=dict)
    dataset: DatasetProvenance
    runtime: RuntimeInfo
    config: RunConfig


class RunArtifacts(BaseModel):
    manifest: RunManifest
    provider_status: list[ProviderStatus]
    retrieval_summaries: list[RetrievalSummary]
    retrieval_rows: list[PerQueryRetrievalResult]
    judge_summaries: list[JudgeSummary] = Field(default_factory=list)
    judge_rows: list[JudgeCaseResult] = Field(default_factory=list)
    fairness_warnings: list[str] = Field(default_factory=list)


def now_utc() -> str:
    return datetime.utcnow().isoformat() + "Z"
