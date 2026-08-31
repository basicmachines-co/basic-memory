"""Agent-task run driver: rich vs POSIX tool surfaces (basic-memory#1401).

Standalone fresh-run driver modeled on ``concurrent_write.run_concurrent_write``:
isolated benchmark home, pinned clean BM checkout, per-surface warm ``bm mcp``
session, per-task fresh corpus copy, settle, grade, artifacts. Only the tool
surface varies between providers; tasks, model, budgets, corpus snapshot, and
prompt preamble are identical (the fairness contract).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from mcp.types import CallToolResult
from rich.console import Console

from basic_memory_benchmarks.agent_tasks.corpus import (
    copy_corpus,
    corpus_checksum,
    snapshot_baseline,
)
from basic_memory_benchmarks.agent_tasks.grading import GradingContext, grade_task
from basic_memory_benchmarks.agent_tasks.loop import (
    AgentLoopError,
    AgentLoopResult,
    ToolOutcome,
    run_agent_loop,
)
from basic_memory_benchmarks.agent_tasks.manifest import load_task_manifest
from basic_memory_benchmarks.agent_tasks.models import (
    AgentTaskResult,
    AgentTasksConfig,
    AgentTasksManifest,
    SkillBreakdown,
    SurfaceEcho,
    SurfaceSummary,
)
from basic_memory_benchmarks.agent_tasks.spec import AgentTaskSpec, spec_needs_project_state
from basic_memory_benchmarks.agent_tasks.surfaces import (
    SURFACES,
    SurfaceUnavailableError,
    ToolSurface,
    read_only_view,
    surface_env,
    verify_surface_tools,
)
from basic_memory_benchmarks.agent_tasks.tasks import select_tasks
from basic_memory_benchmarks.bm_runtime import (
    WarmMcpClient,
    isolated_bm_env,
    resolve_bm_command_prefix,
    settle_index,
)
from basic_memory_benchmarks.concurrent_write import (
    _tool_result_payload,
    resolve_clean_checkout_sha,
)
from basic_memory_benchmarks.fairness import validate_surface_fairness
from basic_memory_benchmarks.llm.runners import LLMRunner, LLMRunnerError, create_runner
from basic_memory_benchmarks.llm.tool_agent import (
    ToolAgentModel,
    ToolDef,
    create_tool_agent_model,
    substitute_placeholders,
)
from basic_memory_benchmarks.models import ProviderStatus, RuntimeInfo
from basic_memory_benchmarks.utils import (
    git_sha,
    run_command,
    runtime_info,
    sha256_file,
    utc_now_iso,
)

console = Console()

# One fixed preamble for every task on every surface (fairness contract). The
# harness does NOT inject the project argument into tool calls — naming the
# project and passing it correctly is part of the measured agent behavior.
TASK_PROMPT_TEMPLATE = """\
You are completing a task in the Basic Memory project "{project}".
Use the available tools to inspect and modify that project's notes.
When you are finished, reply WITHOUT tool calls and end your reply with a fenced
```json code block containing the answer fields the task asks for (or {{}} if none).

Task:
{task_prompt}"""


class SessionTerminatedError(RuntimeError):
    """The MCP session is unusable; remaining tasks on this surface are errored."""


class AgentSession(Protocol):
    """Seam over the per-surface BM MCP session (faked in offline tests)."""

    def start(self) -> None: ...
    def tools(self) -> list[ToolDef]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...
    def stop(self) -> None: ...


@dataclass(frozen=True)
class SurfaceRuntime:
    """Everything a session factory needs to spin up one surface's BM."""

    surface: ToolSurface
    command: str
    args: list[str]
    env: dict[str, str]
    tool_timeout_seconds: float


def tool_outcome_from_result(result: CallToolResult) -> ToolOutcome:
    """Convert an MCP result into loop feedback text plus an error flag.

    Unlike concurrent_write's JSON-only contract, agent tool calls may return
    plain text, so a non-JSON body is NOT an error here; only MCP ``isError``
    and an explicit JSON ``error`` field are.
    """
    texts = [
        text for item in result.content if isinstance(text := getattr(item, "text", None), str)
    ]
    text = "\n".join(texts)
    if result.isError:
        return ToolOutcome(text=text or "Unknown MCP tool error", is_error=True)
    if not text and result.structuredContent is not None:
        text = json.dumps(result.structuredContent)
    payload = _tool_result_payload(result)
    error = payload.get("error") if payload else None
    if error:
        return ToolOutcome(text=text or str(error), is_error=True)
    return ToolOutcome(text=text, is_error=False)


class McpAgentSession:
    """AgentSession over a warm ``bm mcp`` stdio subprocess."""

    def __init__(self, runtime: SurfaceRuntime) -> None:
        # required_tool is a verb shared by BOTH surfaces so the session comes
        # up on any BM build; the authoritative surface check happens in
        # verify_surface_tools against the advertised tool list, which is the
        # only way to produce the informative missing-tools message.
        self._client = WarmMcpClient(
            command=runtime.command,
            args=runtime.args,
            env=runtime.env,
            request_timeout_seconds=runtime.tool_timeout_seconds,
            required_tool="write_note",
        )

    def start(self) -> None:
        self._client.start()

    def tools(self) -> list[ToolDef]:
        return [
            ToolDef(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema),
            )
            for tool in self._client.tools()
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        try:
            result = self._client.call_tool(name, arguments)
        except TimeoutError as exc:
            # A timed-out call leaves the single-slot session with a request in
            # flight; the session cannot be reused.
            raise SessionTerminatedError(f"tool call timed out: {name}") from exc
        except RuntimeError as exc:
            if "not running" in str(exc) or "stopped" in str(exc):
                raise SessionTerminatedError(str(exc)) from exc
            raise
        return tool_outcome_from_result(result)

    def stop(self) -> None:
        self._client.stop()


def _bm_version(prefix: list[str], env: dict[str, str]) -> str | None:
    try:
        result = run_command(prefix + ["--version"], env=env)
    except Exception:
        return None
    return result.stdout.strip() or None


def _errored_result(
    surface: str,
    task: AgentTaskSpec,
    error: str,
    partial: AgentLoopResult | None = None,
) -> AgentTaskResult:
    # Explicit-failure principle: errored tasks carry the error and are
    # excluded from means — never silently zero-scored. When the failure
    # happened after real model calls, the partial loop accounting (tokens,
    # per-turn records) is kept so per-turn.jsonl and the cost columns never
    # under-report spent cost.
    if partial is None:
        return AgentTaskResult(
            surface=surface,
            task_id=task.id,
            skill=task.skill,
            group=task.group,
            passed=False,
            error=error,
        )
    return AgentTaskResult(
        surface=surface,
        task_id=task.id,
        skill=task.skill,
        group=task.group,
        passed=False,
        error=error,
        stopped_reason=partial.stopped_reason,
        turns=partial.turns,
        tool_calls=partial.tool_call_count,
        total_input_tokens=partial.total_input_tokens,
        total_output_tokens=partial.total_output_tokens,
        wall_seconds=round(partial.wall_seconds, 2),
        turn_records=partial.turn_records,
    )


@dataclass(frozen=True)
class TaskProject:
    """The BM project a task runs against: per-task fresh, or shared per group."""

    name: str
    directory: Path


def _prepare_task_project(
    *,
    task: AgentTaskSpec,
    run_id: str,
    corpus_dir: Path,
    surface_home: Path,
    prefix: list[str],
    env: dict[str, str],
    settle_timeout_seconds: float,
    prepared_groups: dict[str, TaskProject],
) -> TaskProject:
    """Copy, register, and settle the project this task runs against.

    Ungrouped (shipped) tasks get a fresh corpus copy each — the fairness
    contract's identical starting state for write-graded tasks. Grouped tasks
    (dataset manifests) ingest ONCE per (surface, group) and every task in the
    group reuses the warm project: copying a multi-hundred-MB persona per
    question is untenable, and grouped runs are read-only so reuse cannot leak
    state between tasks.
    """
    if task.group is None:
        project = TaskProject(
            name=f"at-{run_id}-{task.id}", directory=surface_home / "projects" / task.id
        )
        source_dir = corpus_dir
    else:
        cached = prepared_groups.get(task.group)
        if cached is not None:
            return cached
        project = TaskProject(
            name=f"at-{run_id}-{task.group}", directory=surface_home / "projects" / task.group
        )
        source_dir = corpus_dir / task.group / "docs"
    copy_corpus(source_dir, project.directory)
    run_command(prefix + ["project", "add", project.name, str(project.directory)], env=env)
    settle_index(
        prefix=prefix,
        env=env,
        project_name=project.name,
        timeout_seconds=settle_timeout_seconds,
    )
    if task.group is not None:
        prepared_groups[task.group] = project
    return project


def _run_one_task(
    *,
    surface: ToolSurface,
    task: AgentTaskSpec,
    config: AgentTasksConfig,
    model: ToolAgentModel,
    judge: LLMRunner | None,
    session: AgentSession,
    tool_defs: list[ToolDef],
    prefix: list[str],
    env: dict[str, str],
    surface_home: Path,
    project: TaskProject,
) -> AgentTaskResult:
    # Trigger: every grader reads only the final answer / tool trace.
    # Why: the baseline snapshot loads the whole corpus into memory (a large
    # xAFS persona is hundreds of MB) and the post-loop settle waits on an
    # index no grader will read.
    # Outcome: skip both; project-state graders keep the full flow.
    needs_state = spec_needs_project_state(task)
    baseline = snapshot_baseline(project.directory) if needs_state else {}
    prompt = TASK_PROMPT_TEMPLATE.format(project=project.name, task_prompt=task.prompt)

    def dispatch(name: str, arguments: dict[str, Any]) -> ToolOutcome:
        # {project} placeholders come from the scripted transport, which cannot
        # know per-run project names; a no-op for real model output.
        resolved = substitute_placeholders(arguments, {"project": project.name})
        return session.call_tool(name, resolved)

    loop_result = run_agent_loop(
        model=model,
        dispatch=dispatch,
        tools=tool_defs,
        prompt=prompt,
        budget=config.budget,
    )
    if needs_state:
        settle_index(
            prefix=prefix,
            env=env,
            project_name=project.name,
            timeout_seconds=config.settle_timeout_seconds,
        )
    ctx = GradingContext(
        final_answer=loop_result.final_answer,
        project_dir=project.directory,
        baseline=baseline,
        db_path=surface_home / "config" / "memory.db",
        project_name=project.name,
        turn_records=loop_result.turn_records,
        judge=judge,
    )
    try:
        passed, predicates, judge_usage = grade_task(task, ctx)
    except LLMRunnerError as exc:
        # Judge transport failure after a completed loop: the agent's spent
        # tokens are real cost — keep them on the errored row.
        return _errored_result(surface.name, task, f"grading failed: {exc}", partial=loop_result)
    return AgentTaskResult(
        surface=surface.name,
        task_id=task.id,
        skill=task.skill,
        group=task.group,
        passed=passed,
        stopped_reason=loop_result.stopped_reason,
        turns=loop_result.turns,
        tool_calls=loop_result.tool_call_count,
        total_input_tokens=loop_result.total_input_tokens,
        total_output_tokens=loop_result.total_output_tokens,
        wall_seconds=round(loop_result.wall_seconds, 2),
        final_answer=loop_result.final_answer,
        predicates=predicates,
        judge_input_tokens=judge_usage.input_tokens,
        judge_output_tokens=judge_usage.output_tokens,
        judge_calls=judge_usage.calls,
        turn_records=loop_result.turn_records,
    )


def build_surface_summary(
    surface: str, model_spec: str, results: Sequence[AgentTaskResult]
) -> SurfaceSummary:
    graded = [row for row in results if row.error is None]
    passed_rows = [row for row in graded if row.passed]
    total_input = sum(row.total_input_tokens for row in results)
    total_output = sum(row.total_output_tokens for row in results)
    total_tokens = total_input + total_output
    budget_stops = Counter(
        row.stopped_reason
        for row in graded
        if row.stopped_reason is not None and row.stopped_reason != "final"
    )

    def breakdown(rows: list[AgentTaskResult]) -> SkillBreakdown:
        completed = [row for row in rows if row.error is None and row.passed]
        tokens = sum(row.total_input_tokens + row.total_output_tokens for row in rows)
        return SkillBreakdown(
            total=len(rows),
            passed=len(completed),
            tokens_per_completed=round(tokens / len(completed), 1) if completed else None,
        )

    per_skill = {
        skill: breakdown([row for row in results if row.skill == skill])
        for skill in sorted({row.skill for row in results})
    }
    # tokens/correct per group (persona) IS the corpus-scaling curve for
    # dataset-driven runs; empty for shipped tasks (group is None everywhere).
    per_group = {
        group: breakdown([row for row in results if row.group == group])
        for group in sorted({row.group for row in results if row.group is not None})
    }

    return SurfaceSummary(
        surface=surface,
        model=model_spec,
        tasks_total=len(results),
        tasks_passed=len(passed_rows),
        tasks_errored=len(results) - len(graded),
        budget_stops={str(reason): count for reason, count in sorted(budget_stops.items())},
        pass_rate=round(len(passed_rows) / len(graded), 3) if graded else 0.0,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        # Headline: tokens over ALL attempted tasks per completed task; None
        # (rendered "n/a"), never 0, when nothing completed.
        tokens_per_completed_task=round(total_tokens / len(passed_rows), 1)
        if passed_rows
        else None,
        tokens_per_attempted_task=round(total_tokens / len(results), 1) if results else 0.0,
        mean_tool_calls=round(mean(row.tool_calls for row in graded), 2) if graded else 0.0,
        mean_turns=round(mean(row.turns for row in graded), 2) if graded else 0.0,
        mean_wall_seconds=round(mean(row.wall_seconds for row in graded), 2) if graded else 0.0,
        per_skill=per_skill,
        per_group=per_group,
        judge_input_tokens=sum(row.judge_input_tokens for row in results),
        judge_output_tokens=sum(row.judge_output_tokens for row in results),
        judge_calls=sum(row.judge_calls for row in results),
    )


def _format_tokens_per_completed(value: float | None) -> str:
    return f"{value}" if value is not None else "n/a — 0 tasks completed"


def build_agent_summary_markdown(
    manifest: AgentTasksManifest,
    summaries: Sequence[SurfaceSummary],
    results: Sequence[AgentTaskResult],
    fairness_warnings: Sequence[str],
    statuses: Sequence[ProviderStatus],
) -> str:
    lines = [
        f"# Agent-Task Run `{manifest.run_id}`",
        "",
        "## Provenance",
        "",
        f"- Benchmark SHA: `{manifest.benchmark_git_sha}`",
        f"- BM source: `{manifest.bm_source}`",
        f"- BM resolved SHA: `{manifest.bm_resolved_sha or 'unknown'}`",
        f"- BM version: `{manifest.bm_version or 'unknown'}`",
        f"- Model: `{manifest.model_spec}` — Judge: `{manifest.judge_spec or 'none'}`",
        f"- Corpus: `{manifest.corpus_checksum[:12]}` ({manifest.corpus_file_count} notes)",
        f"- Budget: {manifest.budget.max_turns} turns,"
        f" {manifest.budget.max_total_tokens} tokens,"
        f" {manifest.budget.max_task_seconds}s per task",
        "",
        # The headline column comes first and never appears without the
        # accompanying accuracy/cost columns — reporting must never show
        # accuracy alone.
        "## Results by surface",
        "",
        "| Surface | Tokens/completed | Pass rate | Passed/total | Mean tool calls |"
        " Mean turns | Mean wall s | Total tokens |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in summaries:
        total_tokens = summary.total_input_tokens + summary.total_output_tokens
        lines.append(
            f"| {summary.surface} | {_format_tokens_per_completed(summary.tokens_per_completed_task)} |"
            f" {summary.pass_rate} | {summary.tasks_passed}/{summary.tasks_total} |"
            f" {summary.mean_tool_calls} | {summary.mean_turns} |"
            f" {summary.mean_wall_seconds} | {total_tokens} |"
        )

    # A skipped surface must be visible in the human-facing report, not just
    # surface-status.json — otherwise a rich-only run of `--surfaces rich,posix`
    # reads like a completed A/B.
    skipped = [status for status in statuses if status.state != "ok"]
    if skipped:
        lines += ["", "## Surfaces not run (the table above is NOT a complete A/B)", ""]
        for status in skipped:
            lines.append(f"- {status.provider} ({status.state}): {status.reason or 'no reason'}")

    lines += ["", "## Per skill", ""]
    lines += [
        "| Surface | Skill | Passed/total | Tokens/completed |",
        "| --- | --- | --- | --- |",
    ]
    for summary in summaries:
        for skill, breakdown in summary.per_skill.items():
            lines.append(
                f"| {summary.surface} | {skill} | {breakdown.passed}/{breakdown.total} |"
                f" {_format_tokens_per_completed(breakdown.tokens_per_completed)} |"
            )

    # Per-group = per-persona: tokens/correct against corpus size is the
    # scaling read dataset-driven runs (xAFS) exist to produce.
    if any(summary.per_group for summary in summaries):
        lines += ["", "## Per group (persona)", ""]
        lines += [
            "| Surface | Group | Passed/total | Tokens/completed |",
            "| --- | --- | --- | --- |",
        ]
        for summary in summaries:
            for group, breakdown in summary.per_group.items():
                lines.append(
                    f"| {summary.surface} | {group} | {breakdown.passed}/{breakdown.total} |"
                    f" {_format_tokens_per_completed(breakdown.tokens_per_completed)} |"
                )

    stops = [(s.surface, s.budget_stops) for s in summaries if s.budget_stops]
    if stops:
        lines += ["", "## Budget stops", ""]
        for surface, reasons in stops:
            rendered = ", ".join(f"{reason}: {count}" for reason, count in reasons.items())
            lines.append(f"- {surface}: {rendered}")

    errored = [row for row in results if row.error is not None]
    if errored:
        lines += ["", "## Errored tasks (excluded from means, never zero-scored)", ""]
        for row in errored:
            lines.append(f"- {row.surface}/{row.task_id}: {row.error}")

    if fairness_warnings:
        lines += ["", "## Fairness warnings", ""]
        lines += [f"- {warning}" for warning in fairness_warnings]

    judge_totals = [
        (s.surface, s.judge_input_tokens + s.judge_output_tokens, s.judge_calls)
        for s in summaries
        if s.judge_calls
    ]
    if judge_totals:
        lines += ["", "## Judge usage (never part of the headline)", ""]
        for surface, tokens, calls in judge_totals:
            lines.append(f"- {surface}: {tokens} tokens over {calls} calls")

    return "\n".join(lines).strip() + "\n"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True) + "\n")


def run_agent_tasks(
    config: AgentTasksConfig,
    *,
    model_factory: Callable[[str], ToolAgentModel] = create_tool_agent_model,
    session_factory: Callable[[SurfaceRuntime], AgentSession] | None = None,
    judge_factory: Callable[[str], LLMRunner] = create_runner,
) -> Path:
    """Execute the full agent-task eval and write run artifacts; returns run dir."""
    bm_checkout = Path(config.bm_local_path).expanduser().resolve()
    prefix = resolve_bm_command_prefix(str(bm_checkout))
    bm_resolved_sha = resolve_clean_checkout_sha(bm_checkout)
    config = config.model_copy(update={"bm_local_path": str(bm_checkout)})

    corpus_dir = Path(config.corpus_dir)
    if not corpus_dir.is_dir():
        raise ValueError(f"Corpus dir not found: {corpus_dir}")
    checksum, corpus_file_count = corpus_checksum(corpus_dir)

    # Task source: the shipped Python task set, or a converted dataset manifest
    # (e.g. `convert xafs`) whose grouped, judge-graded rows run through the
    # exact same loop, budgets, and reporting.
    if config.task_manifest:
        task_manifest_path = Path(config.task_manifest)
        # corpus_checksum pins the haystack; this pins the questions. A
        # corrections re-run changes gold answers/rubrics without touching the
        # corpus, and that A/B must be visible in manifest.json.
        task_manifest_sha256 = sha256_file(task_manifest_path)
        tasks = load_task_manifest(task_manifest_path, task_ids=config.task_ids)
    else:
        task_manifest_sha256 = None
        tasks = select_tasks(config.task_ids)

    # Grouped tasks expect corpus_dir to be a converter groups/ dir (one
    # subtree per group); ungrouped tasks expect a flat corpus. Mixing the two
    # in one run would make corpus_dir mean both at once.
    grouped_ids = sorted({task.group for task in tasks if task.group is not None})
    ungrouped_ids = [task.id for task in tasks if task.group is None]
    if grouped_ids and ungrouped_ids:
        raise ValueError(
            f"Task set mixes grouped and ungrouped tasks (grouped={grouped_ids[:3]},"
            f" ungrouped={ungrouped_ids[:3]}); a run must be one or the other"
        )
    for group in grouped_ids:
        group_docs = corpus_dir / group / "docs"
        if not group_docs.is_dir():
            raise ValueError(
                f"Grouped corpus missing {group_docs}; --corpus-dir must point at the"
                " converter's groups/ directory"
            )

    # Model and judge parse before any on-disk state is created, so a bad spec
    # fails fast without leaving an empty benchmark home behind.
    model = model_factory(config.model_spec)
    judge = judge_factory(config.judge_spec) if config.judge_spec else None
    build_session = session_factory or (lambda runtime: McpAgentSession(runtime))

    home = Path("benchmarks/.bm-homes") / f"agent-tasks-{config.run_id}"
    if home.exists():
        raise RuntimeError(f"Home already exists (re-running a run_id is not supported): {home}")
    home.mkdir(parents=True)

    console.print(
        f"[bold]agent-tasks[/bold] run_id={config.run_id} surfaces={config.surfaces}"
        f" tasks={len(tasks)} model={model.spec}"
    )

    statuses: list[ProviderStatus] = []
    echoes: list[SurfaceEcho] = []
    results: list[AgentTaskResult] = []
    bm_version: str | None = None

    for surface_name in config.surfaces:
        surface = SURFACES[surface_name]
        if config.task_manifest:
            # Trigger: manifest-driven (grouped) run.
            # Why: tasks in a group share one warm project, and a write_note
            # from an earlier question would pollute later questions' haystack.
            # Outcome: the shared write verbs are dropped from BOTH surfaces
            # symmetrically (fairness preserved; echoed via SurfaceEcho).
            surface = read_only_view(surface)
        surface_home = home / surface.name
        (surface_home / "default-home").mkdir(parents=True)
        env = surface_env(surface, isolated_bm_env(surface_home))
        if bm_version is None:
            bm_version = _bm_version(prefix, env)
        runtime = SurfaceRuntime(
            surface=surface,
            command=prefix[0],
            args=prefix[1:] + ["mcp"],
            env=env,
            tool_timeout_seconds=config.tool_timeout_seconds,
        )
        session = build_session(runtime)
        console.print(f"Starting `bm mcp` session for surface [cyan]{surface.name}[/cyan]...")
        session.start()
        try:
            available = session.tools()
            observed_names = sorted(tool.name for tool in available)
            echoes.append(
                SurfaceEcho(
                    name=surface.name,
                    config_overrides=dict(surface.config_overrides),
                    tool_allowlist=list(surface.tool_allowlist),
                    observed_tools=observed_names,
                )
            )
            try:
                verify_surface_tools(surface, observed_names, bm_version=bm_version)
            except SurfaceUnavailableError as exc:
                # Never silently dropped: the surface is recorded as skipped
                # (default) or aborts the run (--strict-surfaces).
                if not config.allow_surface_skip:
                    raise
                console.print(f"[yellow]Skipping surface {surface.name}[/yellow]: {exc}")
                statuses.append(
                    ProviderStatus(provider=surface.name, state="skipped", reason=str(exc))
                )
                continue

            by_name = {tool.name: tool for tool in available}
            # Deterministic order: schemas reach the model in allowlist order.
            tool_defs = [by_name[name] for name in surface.tool_allowlist]

            prepared_groups: dict[str, TaskProject] = {}
            session_error: str | None = None
            for task in tasks:
                if session_error is not None:
                    results.append(
                        _errored_result(
                            surface.name, task, f"mcp session terminated: {session_error}"
                        )
                    )
                    continue
                console.print(f"  [{surface.name}] task [cyan]{task.id}[/cyan]...")
                try:
                    project = _prepare_task_project(
                        task=task,
                        run_id=config.run_id,
                        corpus_dir=corpus_dir,
                        surface_home=surface_home,
                        prefix=prefix,
                        env=env,
                        settle_timeout_seconds=config.settle_timeout_seconds,
                        prepared_groups=prepared_groups,
                    )
                    result = _run_one_task(
                        surface=surface,
                        task=task,
                        config=config,
                        model=model,
                        judge=judge,
                        session=session,
                        tool_defs=tool_defs,
                        prefix=prefix,
                        env=env,
                        surface_home=surface_home,
                        project=project,
                    )
                except AgentLoopError as exc:
                    # The loop wraps every mid-task failure with the partial
                    # accounting; the cause decides task-level vs surface-level
                    # handling. Anything else is a harness bug: abort loudly.
                    if isinstance(exc.cause, SessionTerminatedError):
                        session_error = str(exc.cause)
                        results.append(
                            _errored_result(
                                surface.name,
                                task,
                                f"mcp session terminated: {session_error}",
                                partial=exc.partial,
                            )
                        )
                        continue
                    if isinstance(exc.cause, LLMRunnerError):
                        results.append(
                            _errored_result(surface.name, task, str(exc.cause), partial=exc.partial)
                        )
                        continue
                    raise
                results.append(result)
            statuses.append(ProviderStatus(provider=surface.name, state="ok"))
        finally:
            session.stop()

    ok_surfaces = {status.provider for status in statuses if status.state == "ok"}
    fairness_warnings = validate_surface_fairness(
        {surface: [row for row in results if row.surface == surface] for surface in ok_surfaces}
    )
    summaries = [
        build_surface_summary(
            surface, model.spec, [row for row in results if row.surface == surface]
        )
        for surface in config.surfaces
        if surface in ok_surfaces
    ]

    manifest = AgentTasksManifest(
        run_id=config.run_id,
        created_at_utc=utc_now_iso(),
        benchmark_git_sha=git_sha(Path(".")) or "unknown",
        bm_source=config.bm_source,
        bm_resolved_sha=bm_resolved_sha,
        bm_version=bm_version,
        home_dir=str(home),
        model_spec=model.spec,
        judge_spec=judge.spec if judge is not None else None,
        budget=config.budget,
        corpus_checksum=checksum,
        corpus_file_count=corpus_file_count,
        task_manifest_sha256=task_manifest_sha256,
        task_ids=[task.id for task in tasks],
        surfaces=echoes,
        runtime=RuntimeInfo(
            os=runtime_info()[0],
            python_version=runtime_info()[1],
            started_at_utc=utc_now_iso(),
        ),
        config=config,
    )

    run_dir = Path(config.output_root) / config.run_id
    _write_json(run_dir / "manifest.json", manifest.model_dump(mode="json"))
    _write_json(
        run_dir / "surface-status.json",
        [status.model_dump(mode="json") for status in statuses],
    )
    _write_jsonl(
        run_dir / "per-turn.jsonl",
        [
            {"surface": row.surface, "task_id": row.task_id, **record.model_dump(mode="json")}
            for row in results
            for record in row.turn_records
        ],
    )
    _write_jsonl(
        run_dir / "per-task-agent.jsonl",
        [row.model_dump(mode="json", exclude={"turn_records"}) for row in results],
    )
    _write_json(
        run_dir / "agent-tasks-summary.json",
        {
            "surfaces": [summary.model_dump(mode="json") for summary in summaries],
            "fairness_warnings": list(fairness_warnings),
        },
    )
    (run_dir / "summary.md").write_text(
        build_agent_summary_markdown(manifest, summaries, results, fairness_warnings, statuses),
        encoding="utf-8",
    )

    for summary in summaries:
        console.print(
            f"[bold]{summary.surface}[/bold]: {summary.tasks_passed}/{summary.tasks_total} passed,"
            f" tokens/completed = {_format_tokens_per_completed(summary.tokens_per_completed_task)},"
            f" mean tool calls {summary.mean_tool_calls}"
        )
    for warning in fairness_warnings:
        console.print(f"[yellow]fairness[/yellow]: {warning}")
    return run_dir
