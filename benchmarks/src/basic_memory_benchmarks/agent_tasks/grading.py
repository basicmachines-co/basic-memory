"""Deterministic and judge grading for agent tasks.

Deterministic predicates read the final answer, the settled project directory,
and the run's isolated SQLite index; ``JudgeRubric`` goes through the package's
existing ``LLMRunner`` judge seam. A malformed final answer is a task failure
(failed predicates with detail), never a harness error; judge/model transport
failures raise and become explicit task errors in the driver.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from basic_memory_benchmarks.agent_tasks.models import PredicateResult, TurnRecord
from basic_memory_benchmarks.agent_tasks.spec import (
    NEW_FILE_SELECTOR,
    AgentTaskSpec,
    AnswerContains,
    AnswerMatches,
    AnswerSetEquals,
    FileLineDiff,
    FrontmatterEquals,
    FrontmatterListLen,
    FrontmatterMatches,
    Grader,
    JudgeRubric,
    MarkerAbsent,
    MarkerPresent,
    NewNoteUnder,
    NoteCountDelta,
    NotesUntouched,
    ObservationLines,
    RelationResolves,
    ToolCalled,
)
from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner

_FENCED_JSON_PATTERN = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

AGENT_JUDGE_PROMPT_TEMPLATE = """\
You are grading an agent's final answer against a rubric.

Rubric:
{rubric}

Final answer:
{answer}

Reply with exactly one line: CORRECT or INCORRECT, followed by " - " and a
one-sentence reason."""


@dataclass
class JudgeUsage:
    """Accumulates judge-side token usage across a task's judge calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, result: LLMResult) -> None:
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.calls += 1


@dataclass
class GradingContext:
    final_answer: str | None
    project_dir: Path
    # Baseline snapshot taken after seed settle: relpath -> full file text.
    # (Content, not just a checksum, so line-diff predicates need no second
    # directory copy — the corpus is tiny.)
    baseline: Mapping[str, str]
    db_path: Path
    project_name: str
    turn_records: Sequence[TurnRecord] = field(default_factory=tuple)
    judge: LLMRunner | None = None


# --- Answer helpers ---


def extract_final_json(final_answer: str | None) -> dict[str, object] | None:
    """The LAST fenced ```json block of the final answer, parsed; None if absent."""
    if not final_answer:
        return None
    blocks = _FENCED_JSON_PATTERN.findall(final_answer)
    if not blocks:
        return None
    try:
        payload = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_answer_item(value: str) -> str:
    """Comparable form for permalinks/titles: strip, drop leading '/' and '.md'."""
    return value.strip().lstrip("/").removesuffix(".md").lower()


def strip_own_project_prefix(item: str, project_name: str) -> str:
    """Drop the task's OWN project-name prefix from a normalized answer item.

    Agents quote permalinks exactly as tools return them — ``<project>/<permalink>``
    — while gold values are project-relative. Only this task's project name is
    stripped: an item carrying a DIFFERENT project's prefix is genuinely wrong
    (cross-project leakage) and must keep failing.
    """
    return item.removeprefix(normalize_answer_item(project_name) + "/")


# --- File helpers ---


def _markdown_relpaths(project_dir: Path) -> list[str]:
    return sorted(
        str(path.relative_to(project_dir)) for path in project_dir.rglob("*.md") if path.is_file()
    )


def _select_files(ctx: GradingContext, selector: str) -> list[str]:
    current = _markdown_relpaths(ctx.project_dir)
    if selector == NEW_FILE_SELECTOR:
        return [relpath for relpath in current if relpath not in ctx.baseline]
    return [relpath for relpath in current if Path(relpath).match(selector)]


def _single_file(ctx: GradingContext, selector: str) -> tuple[str | None, str]:
    matches = _select_files(ctx, selector)
    if len(matches) == 1:
        return matches[0], ""
    return None, f"selector '{selector}' matched {len(matches)} files: {matches[:5]}"


def _frontmatter_value(text: str, dotted_key: str) -> object | None:
    metadata: object = frontmatter.loads(text).metadata
    for part in dotted_key.split("."):
        if not isinstance(metadata, dict) or part not in metadata:
            return None
        metadata = metadata[part]
    return metadata


# --- Predicate evaluation ---


def _result(grader: Grader, passed: bool, detail: str) -> PredicateResult:
    kind = type(grader).__name__
    return PredicateResult(
        name=f"{kind}({_grader_label(grader)})",
        kind=kind,
        passed=passed,
        required=grader.required,
        detail=detail,
    )


def _grader_label(grader: Grader) -> str:
    match grader:
        case AnswerSetEquals(key=key):
            return key
        case MarkerPresent(marker=marker) | MarkerAbsent(marker=marker):
            return marker
        case AnswerContains(needle=needle):
            return needle
        case AnswerMatches(pattern=pattern):
            return pattern
        case NoteCountDelta(delta=delta):
            return f"{delta:+d}"
        case NewNoteUnder(prefix=prefix):
            return prefix
        case NotesUntouched():
            return "*"
        case FrontmatterEquals(path=path, key=key) | FrontmatterMatches(path=path, key=key):
            return f"{path}:{key}"
        case FrontmatterListLen(path=path, key=key):
            return f"{path}:{key}"
        case ObservationLines(path=path):
            return path
        case RelationResolves(source_permalink=source):
            return source
        case FileLineDiff(path=path):
            return path
        case ToolCalled(name_pattern=pattern):
            return pattern
        case JudgeRubric():
            return "rubric"


def _eval_answer_set(grader: AnswerSetEquals, ctx: GradingContext) -> PredicateResult:
    payload = extract_final_json(ctx.final_answer)
    if payload is None:
        return _result(grader, False, "no parseable fenced JSON block in the final answer")
    raw = payload.get(grader.key)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return _result(grader, False, f"answer key '{grader.key}' is not a list of strings")
    got = {strip_own_project_prefix(normalize_answer_item(item), ctx.project_name) for item in raw}
    gold = {normalize_answer_item(item) for item in grader.gold}
    if got == gold:
        return _result(grader, True, f"{len(gold)} items match")
    missing = sorted(gold - got)
    extra = sorted(got - gold)
    return _result(grader, False, f"missing={missing[:5]} extra={extra[:5]}")


def _eval_new_note_under(grader: NewNoteUnder, ctx: GradingContext) -> PredicateResult:
    new_files = _select_files(ctx, NEW_FILE_SELECTOR)
    if not new_files:
        return _result(grader, False, "no new notes since baseline")
    # is_relative_to gives directory semantics regardless of a trailing slash:
    # "tasks" and "tasks/" both contain "tasks/x.md" and neither contains
    # "tasks-archive/x.md", which a bare startswith("tasks") would accept.
    misplaced = [
        relpath for relpath in new_files if not Path(relpath).is_relative_to(grader.prefix)
    ]
    if misplaced:
        return _result(grader, False, f"new notes outside {grader.prefix}: {misplaced[:5]}")
    return _result(grader, True, f"{len(new_files)} new note(s) under {grader.prefix}")


def _eval_notes_untouched(grader: NotesUntouched, ctx: GradingContext) -> PredicateResult:
    changed: list[str] = []
    for relpath, baseline_text in ctx.baseline.items():
        if any(Path(relpath).match(glob) for glob in grader.except_globs):
            continue
        current = ctx.project_dir / relpath
        if not current.is_file() or current.read_text(encoding="utf-8") != baseline_text:
            changed.append(relpath)
    if changed:
        return _result(grader, False, f"{len(changed)} baseline notes changed: {changed[:5]}")
    return _result(grader, True, "baseline notes untouched")


def _eval_frontmatter(
    grader: FrontmatterEquals | FrontmatterMatches | FrontmatterListLen,
    ctx: GradingContext,
) -> PredicateResult:
    relpath, problem = _single_file(ctx, grader.path)
    if relpath is None:
        return _result(grader, False, problem)
    text = (ctx.project_dir / relpath).read_text(encoding="utf-8")
    actual = _frontmatter_value(text, grader.key)
    if actual is None:
        return _result(grader, False, f"{relpath} has no frontmatter key '{grader.key}'")
    match grader:
        case FrontmatterEquals(value=value):
            # str-comparison fallback absorbs YAML type drift (e.g. quoted ints).
            passed = actual == value or str(actual) == str(value)
            return _result(grader, passed, f"{relpath}: {grader.key}={actual!r}")
        case FrontmatterMatches(pattern=pattern):
            passed = re.search(pattern, str(actual)) is not None
            return _result(grader, passed, f"{relpath}: {grader.key}={actual!r}")
        case FrontmatterListLen(length=length):
            if not isinstance(actual, list):
                return _result(grader, False, f"{relpath}: {grader.key} is not a list")
            return _result(
                grader,
                len(actual) == length,
                f"{relpath}: len({grader.key})={len(actual)}, expected {length}",
            )


def _eval_relation_resolves(grader: RelationResolves, ctx: GradingContext) -> PredicateResult:
    if not ctx.db_path.exists():
        raise RuntimeError(f"Index database not found: {ctx.db_path}")
    # Plain connection issuing SELECTs only; the BM process is idle post-settle.
    connection = sqlite3.connect(ctx.db_path)
    try:
        row = connection.execute(
            "SELECT id FROM project WHERE name = ?", (ctx.project_name,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Project '{ctx.project_name}' not found in {ctx.db_path}")
        project_id = int(row[0])
        # Stored permalinks are project-prefixed (verified against a live run
        # DB: 'at-<run>-<task>/notes/redis-cache-tuning'), while task specs
        # use project-relative gold — match either form, same policy as
        # strip_own_project_prefix for answer-set graders.
        query = (
            "SELECT target.permalink, r.relation_type"
            " FROM relation r"
            " JOIN entity source ON r.from_id = source.id"
            " JOIN entity target ON r.to_id = target.id"
            " WHERE source.project_id = ? AND source.permalink IN (?, ?)"
            " AND r.to_id IS NOT NULL"
        )
        prefixed_source = f"{ctx.project_name}/{grader.source_permalink}"
        rows = connection.execute(
            query, (project_id, grader.source_permalink, prefixed_source)
        ).fetchall()
    finally:
        connection.close()

    targets = {normalize_answer_item(item) for item in grader.targets}
    for target_permalink, relation_type in rows:
        resolved = strip_own_project_prefix(
            normalize_answer_item(str(target_permalink)), ctx.project_name
        )
        if resolved not in targets:
            continue
        if grader.relation_type is not None and relation_type != grader.relation_type:
            continue
        return _result(
            grader, True, f"{grader.source_permalink} -{relation_type}-> {target_permalink}"
        )
    return _result(
        grader,
        False,
        f"no resolved relation from {grader.source_permalink} to {sorted(targets)}"
        f" (found {len(rows)} resolved relations)",
    )


def _eval_file_line_diff(grader: FileLineDiff, ctx: GradingContext) -> PredicateResult:
    relpath, problem = _single_file(ctx, grader.path)
    if relpath is None:
        return _result(grader, False, problem)
    baseline_text = ctx.baseline.get(relpath)
    if baseline_text is None:
        return _result(grader, False, f"{relpath} has no baseline to diff against")
    current_lines = Counter((ctx.project_dir / relpath).read_text(encoding="utf-8").splitlines())
    baseline_lines = Counter(baseline_text.splitlines())
    removed = list((baseline_lines - current_lines).elements())
    added = list((current_lines - baseline_lines).elements())
    if len(removed) != 1 or len(added) != 1:
        return _result(
            grader, False, f"{relpath}: {len(removed)} lines removed, {len(added)} added"
        )
    removed_ok = re.search(grader.removed_pattern, removed[0]) is not None
    added_ok = re.search(grader.added_pattern, added[0]) is not None
    return _result(
        grader,
        removed_ok and added_ok,
        f"removed={removed[0]!r} added={added[0]!r}",
    )


def _parse_judge_line(raw: str) -> tuple[bool, str]:
    upper = raw.upper()
    if re.search(r"\bINCORRECT\b", upper):
        return False, raw.strip().splitlines()[0][:200]
    if re.search(r"\bCORRECT\b", upper):
        return True, raw.strip().splitlines()[0][:200]
    raise ValueError(f"Judge returned neither CORRECT nor INCORRECT: {raw[:200]}")


def _eval_judge(grader: JudgeRubric, ctx: GradingContext, usage: JudgeUsage) -> PredicateResult:
    if ctx.judge is None:
        # Fail fast: the CLI refuses judge-graded tasks without --judge, so
        # reaching here is a harness bug, not a task outcome.
        raise RuntimeError("JudgeRubric grader requires a judge runner")
    if not ctx.final_answer:
        return _result(grader, False, "no final answer to judge")
    prompt = AGENT_JUDGE_PROMPT_TEMPLATE.format(rubric=grader.rubric, answer=ctx.final_answer)
    result = ctx.judge.complete(prompt)
    usage.add(result)
    passed, reason = _parse_judge_line(result.text)
    return _result(grader, passed, reason)


def evaluate_grader(
    grader: Grader, ctx: GradingContext, usage: JudgeUsage | None = None
) -> PredicateResult:
    match grader:
        case AnswerSetEquals():
            return _eval_answer_set(grader, ctx)
        case MarkerPresent(marker=marker):
            present = ctx.final_answer is not None and marker in ctx.final_answer
            return _result(grader, present, "marker present" if present else "marker missing")
        case MarkerAbsent(marker=marker):
            present = ctx.final_answer is not None and marker in ctx.final_answer
            return _result(
                grader, not present, "decoy marker present" if present else "decoy absent"
            )
        case AnswerContains(needle=needle):
            found = ctx.final_answer is not None and needle.lower() in ctx.final_answer.lower()
            return _result(grader, found, f"'{needle}' {'found' if found else 'missing'}")
        case AnswerMatches(pattern=pattern):
            found = ctx.final_answer is not None and (
                re.search(pattern, ctx.final_answer, re.IGNORECASE) is not None
            )
            return _result(grader, found, f"/{pattern}/ {'found' if found else 'missing'}")
        case NoteCountDelta(delta=delta):
            actual = len(_markdown_relpaths(ctx.project_dir)) - len(ctx.baseline)
            return _result(grader, actual == delta, f"note count delta {actual:+d}")
        case NewNoteUnder():
            return _eval_new_note_under(grader, ctx)
        case NotesUntouched():
            return _eval_notes_untouched(grader, ctx)
        case FrontmatterEquals() | FrontmatterMatches() | FrontmatterListLen():
            return _eval_frontmatter(grader, ctx)
        case ObservationLines(path=path, pattern=pattern, min_count=min_count):
            relpath, problem = _single_file(ctx, path)
            if relpath is None:
                return _result(grader, False, problem)
            text = (ctx.project_dir / relpath).read_text(encoding="utf-8")
            count = sum(1 for line in text.splitlines() if re.search(pattern, line))
            return _result(grader, count >= min_count, f"{relpath}: {count} matching lines")
        case RelationResolves():
            return _eval_relation_resolves(grader, ctx)
        case FileLineDiff():
            return _eval_file_line_diff(grader, ctx)
        case ToolCalled(name_pattern=pattern):
            called = any(
                record.kind == "tool"
                and record.tool_name is not None
                and re.search(pattern, record.tool_name)
                for record in ctx.turn_records
            )
            return _result(grader, called, "matching tool called" if called else "not called")
        case JudgeRubric():
            return _eval_judge(grader, ctx, usage if usage is not None else JudgeUsage())


def grade_task(
    spec: AgentTaskSpec, ctx: GradingContext
) -> tuple[bool, list[PredicateResult], JudgeUsage]:
    usage = JudgeUsage()
    predicates = [evaluate_grader(grader, ctx, usage) for grader in spec.graders]
    passed = all(predicate.passed for predicate in predicates if predicate.required)
    return passed, predicates, usage
