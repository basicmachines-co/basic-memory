"""Configuration and artifact schemas for the agent-task eval (basic-memory#1401)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from basic_memory_benchmarks.models import RuntimeInfo

# "error" marks a loop that died mid-task (model or dispatch failure): the
# partial accounting is kept on the errored row, never counted as a budget stop.
StopReason = Literal["final", "turns", "tokens", "wall_clock", "error"]

# run_id is not merely a label. The driver splices it into a `bm project add`
# argv token (project names are `{run_id}-{task.id}`) and into two filesystem
# paths (the benchmark home and the run dir), so it must be safe as both. A
# leading "-" makes the BM CLI parse the project name as options and abort with
# "No such option: -t" — naming a flag the operator never typed — and a path
# separator would place run artifacts outside the run dir.
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class AgentBudget(BaseModel):
    """Per-task budgets; identical across surfaces (fairness contract)."""

    max_turns: int = Field(default=20, ge=1)
    max_total_tokens: int = Field(default=200_000, ge=1)
    max_task_seconds: float = Field(default=300.0, gt=0.0)


class AgentTasksConfig(BaseModel):
    run_id: str
    surfaces: list[str] = Field(default_factory=lambda: ["rich"])
    task_ids: list[str] = Field(default_factory=list)  # empty = all shipped tasks
    # Converted-dataset tasks.json (e.g. convert xafs); None runs the shipped
    # task set. Manifest runs are grouped and read-only (see driver).
    task_manifest: str | None = None
    model_spec: str
    # None = the temperature parameter is omitted from requests entirely
    # (Claude 5 endpoints reject it). Provenance: the value the model factory
    # used for this run.
    # allow_inf_nan=False because nan/inf survive float() and Pydantic's plain
    # float schema: they would reach the request body, where httpx raises a bare
    # ValueError mid-run. Worse for provenance, model_dump_json() encodes them
    # as null — which is exactly this field's "temperature omitted" sentinel, so
    # the recorded run config would silently misreport what was sent.
    model_temperature: float | None = Field(default=0.0, allow_inf_nan=False)
    judge_spec: str | None = None
    corpus_dir: str = "benchmarks/datasets/agent-tasks/corpus"
    output_root: str = "benchmarks/runs"
    bm_source: str = "local-checkout"
    bm_local_path: str
    budget: AgentBudget = Field(default_factory=AgentBudget)
    tool_timeout_seconds: float = Field(default=120.0, gt=0.0)
    settle_timeout_seconds: float = Field(default=180.0, gt=0.0)
    allow_surface_skip: bool = True

    @field_validator("run_id")
    @classmethod
    def _run_id_is_argv_and_path_safe(cls, value: str) -> str:
        # Rejected here rather than in the driver so the run dies before any
        # setup cost: without this the first `bm project add` fails only after
        # the benchmark home, a warm `bm mcp` subprocess, and a full corpus copy
        # exist — and run_command captures stderr, so the operator sees a bare
        # CalledProcessError exit status, not the CLI's own complaint. The
        # abandoned home then blocks re-running the same run_id.
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "run_id must start with a letter, digit, or underscore and use only "
                "letters, digits, '.', '_', or '-'; it becomes both a CLI argument "
                f"and a path component, got {value!r}"
            )
        return value


class TurnRecord(BaseModel):
    """One model turn or one tool dispatch inside a task's agent loop."""

    turn_index: int
    kind: Literal["model", "tool"]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    tool_call_count: int = 0
    finalized: bool = False
    tool_name: str | None = None
    arguments_chars: int = 0
    result_chars: int = 0
    is_error: bool = False
    # Set only on error turns: the arguments that provoked the error and the
    # text the model was told. Success payloads stay out of the artifact (large,
    # and reproducible from the corpus); an error that recurs run after run is
    # not diagnosable from lengths alone, which is what the posix `find` error
    # in continue-spec9 was across three runs.
    arguments_excerpt: str | None = None
    error_excerpt: str | None = None


class PredicateResult(BaseModel):
    name: str
    kind: str
    passed: bool
    required: bool
    detail: str


class AgentTaskResult(BaseModel):
    surface: str
    task_id: str
    skill: str
    # Corpus group (persona) for dataset-driven tasks; None for shipped tasks.
    group: str | None = None
    passed: bool
    stopped_reason: StopReason | None = None
    turns: int = 0
    tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    wall_seconds: float = 0.0
    final_answer: str | None = None
    predicates: list[PredicateResult] = Field(default_factory=list)
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_calls: int = 0
    # Harness/model/session failure: errored tasks are excluded from means and
    # never zero-scored (the scoring/beam.py explicit-failure principle).
    error: str | None = None
    # Per-turn accounting; serialized into per-turn.jsonl, excluded from the
    # per-task rows to keep them scannable.
    turn_records: list[TurnRecord] = Field(default_factory=list)


class SkillBreakdown(BaseModel):
    total: int
    passed: int
    tokens_per_completed: float | None = None


class SurfaceSummary(BaseModel):
    surface: str
    model: str
    tasks_total: int
    tasks_passed: int
    tasks_errored: int
    budget_stops: dict[str, int] = Field(default_factory=dict)
    pass_rate: float
    total_input_tokens: int
    total_output_tokens: int
    # The headline: total agent tokens over ALL attempted tasks divided by
    # completed tasks. None (never 0) when nothing completed.
    tokens_per_completed_task: float | None
    tokens_per_attempted_task: float
    mean_tool_calls: float
    mean_turns: float
    mean_wall_seconds: float
    per_skill: dict[str, SkillBreakdown] = Field(default_factory=dict)
    # Per-group (persona) breakdown for dataset-driven runs — tokens/correct
    # per persona is the corpus-scaling curve; empty for shipped-task runs.
    per_group: dict[str, SkillBreakdown] = Field(default_factory=dict)
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_calls: int = 0


class SurfaceEcho(BaseModel):
    """Full surface definition echoed into the manifest — the fairness audit trail."""

    name: str
    config_overrides: dict[str, str]
    tool_allowlist: list[str]
    observed_tools: list[str] = Field(default_factory=list)


class AgentTasksManifest(BaseModel):
    run_id: str
    created_at_utc: str
    benchmark_git_sha: str
    bm_source: str
    bm_resolved_sha: str
    bm_version: str | None = None
    home_dir: str
    model_spec: str
    judge_spec: str | None = None
    budget: AgentBudget
    corpus_checksum: str
    corpus_file_count: int
    # Pins the tasks.json a dataset-driven run executed. corpus_checksum alone
    # cannot distinguish two runs differing only in --corrections (changed gold
    # answers/rubrics, excluded questions) — exactly the pre/post-audit A/B.
    # None for shipped-task runs.
    task_manifest_sha256: str | None = None
    task_ids: list[str]
    surfaces: list[SurfaceEcho]
    runtime: RuntimeInfo
    config: AgentTasksConfig
