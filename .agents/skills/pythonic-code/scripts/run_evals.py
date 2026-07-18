#!/usr/bin/env python3
"""Run isolated paired evaluations for the pythonic-code skill."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
EVALS_PATH = SKILL_DIR / "evals" / "evals.json"
WORKSPACE_CONTEXT_PATHS = (
    Path("evals/context/AGENTS.md"),
    Path("evals/context/pyproject.toml"),
)
EVAL_NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
IGNORED_OUTPUT_NAMES = {
    ".agents",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: int
    name: str
    prompt: str
    expected_output: str
    files: tuple[Path, ...]
    assertions: tuple[str, ...]


def load_eval_cases() -> tuple[EvalCase, ...]:
    payload: object = json.loads(EVALS_PATH.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{EVALS_PATH} must contain a JSON object")
    if payload.get("skill_name") != "pythonic-code":
        raise ValueError("eval skill_name must be 'pythonic-code'")

    raw_cases = payload.get("evals")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("evals must be a non-empty list")

    cases: list[EvalCase] = []
    for position, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"eval at position {position} must be an object")

        case_id = raw_case.get("id")
        name = raw_case.get("eval_name")
        prompt = raw_case.get("prompt")
        expected_output = raw_case.get("expected_output")
        raw_files = raw_case.get("files")
        raw_assertions = raw_case.get("assertions")

        if not isinstance(case_id, int) or isinstance(case_id, bool):
            raise ValueError(f"eval at position {position} must have an integer id")
        if not isinstance(name, str) or EVAL_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(f"eval {case_id} must have a lowercase hyphenated eval_name")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"eval {case_id} must have a non-empty prompt")
        if not isinstance(expected_output, str) or not expected_output.strip():
            raise ValueError(f"eval {case_id} must have a non-empty expected_output")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError(f"eval {case_id} must have at least one input file")
        if not all(isinstance(value, str) and value for value in raw_files):
            raise ValueError(f"eval {case_id} files must be non-empty strings")
        if not isinstance(raw_assertions, list) or not raw_assertions:
            raise ValueError(f"eval {case_id} must have at least one assertion")
        if not all(isinstance(value, str) and value for value in raw_assertions):
            raise ValueError(f"eval {case_id} assertions must be non-empty strings")

        files = tuple(Path(value) for value in raw_files)
        assertions = tuple(raw_assertions)
        cases.append(
            EvalCase(
                id=case_id,
                name=name,
                prompt=prompt,
                expected_output=expected_output,
                files=files,
                assertions=assertions,
            )
        )

    validate_eval_cases(cases)
    return tuple(cases)


def validate_eval_cases(cases: list[EvalCase]) -> None:
    ids = [case.id for case in cases]
    names = [case.name for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("eval ids must be unique")
    if len(names) != len(set(names)):
        raise ValueError("eval names must be unique")

    for relative_path in WORKSPACE_CONTEXT_PATHS:
        if not (SKILL_DIR / relative_path).is_file():
            raise ValueError(f"eval workspace context does not exist: {relative_path}")

    for case in cases:
        for relative_path in case.files:
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(f"eval {case.id} has unsafe file path: {relative_path}")
            source_path = SKILL_DIR / relative_path
            if not source_path.is_file():
                raise ValueError(f"eval {case.id} input does not exist: {relative_path}")
            if source_path.suffix == ".py":
                ast.parse(source_path.read_text(), filename=str(source_path))


def select_eval_cases(cases: tuple[EvalCase, ...], selector: str) -> tuple[EvalCase, ...]:
    if selector == "all":
        return cases

    selected = tuple(case for case in cases if str(case.id) == selector or case.name == selector)
    if selected:
        return selected

    options = ", ".join(f"{case.id} ({case.name})" for case in cases)
    raise ValueError(f"unknown eval case {selector!r}; choose all or one of: {options}")


def copy_skill_without_eval_answers(work_dir: Path) -> None:
    destination = work_dir / ".agents" / "skills" / "pythonic-code"

    def exclude_eval_material(directory: str, names: list[str]) -> list[str]:
        excluded = {"evals", "justfile", "Justfile", "__pycache__"}
        if Path(directory).resolve() == (SKILL_DIR / "scripts").resolve():
            excluded.add("run_evals.py")
        return [name for name in names if name in excluded or name.endswith(".pyc")]

    shutil.copytree(SKILL_DIR, destination, ignore=exclude_eval_material)


def prepare_work_directory(run_dir: Path, case: EvalCase, with_skill: bool) -> Path:
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True)
    for relative_path in WORKSPACE_CONTEXT_PATHS:
        shutil.copy2(SKILL_DIR / relative_path, work_dir / relative_path.name)
    for relative_path in case.files:
        destination = work_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SKILL_DIR / relative_path, destination)
    if with_skill:
        copy_skill_without_eval_answers(work_dir)
    return work_dir


def copy_run_outputs(work_dir: Path, outputs_dir: Path) -> None:
    def ignore_runtime_files(_directory: str, names: list[str]) -> list[str]:
        return [name for name in names if name in IGNORED_OUTPUT_NAMES or name.endswith(".pyc")]

    shutil.copytree(work_dir, outputs_dir, ignore=ignore_runtime_files)


def read_total_tokens(events_path: Path) -> int | None:
    total_tokens: int | None = None
    for line in events_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue

        reported_total = usage.get("total_tokens")
        if isinstance(reported_total, int):
            total_tokens = reported_total
            continue

        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens
    return total_tokens


def run_codex_trial(
    codex_path: str,
    iteration_dir: Path,
    case: EvalCase,
    *,
    with_skill: bool,
    model: str | None,
) -> int:
    mode = "with_skill" if with_skill else "without_skill"
    run_dir = iteration_dir / f"eval-{case.id}-{case.name}" / mode
    work_dir = prepare_work_directory(run_dir, case, with_skill)
    events_path = run_dir / "events.jsonl"
    stderr_path = run_dir / "stderr.txt"
    final_path = run_dir / "final.txt"

    prompt = f"Use $pythonic-code. {case.prompt}" if with_skill else case.prompt
    command = [
        codex_path,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--json",
        "-C",
        str(work_dir),
        "-o",
        str(final_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)

    print(f"Running eval {case.id} ({case.name}) [{mode}]", flush=True)
    started_at = time.perf_counter()
    with events_path.open("w") as events_file, stderr_path.open("w") as stderr_file:
        result = subprocess.run(
            command,
            stdout=events_file,
            stderr=stderr_file,
            text=True,
            check=False,
        )
    duration_ms = round((time.perf_counter() - started_at) * 1000)

    copy_run_outputs(work_dir, run_dir / "outputs")
    timing = {
        "total_tokens": read_total_tokens(events_path),
        "duration_ms": duration_ms,
        "exit_code": result.returncode,
    }
    (run_dir / "timing.json").write_text(json.dumps(timing, indent=2) + "\n")
    if result.returncode != 0:
        print(f"Trial failed; see {stderr_path}", file=sys.stderr)
    return result.returncode


def write_case_metadata(iteration_dir: Path, case: EvalCase) -> None:
    case_dir = iteration_dir / f"eval-{case.id}-{case.name}"
    case_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": case.id,
        "eval_name": case.name,
        "expected_output": case.expected_output,
        "assertions": case.assertions,
    }
    (case_dir / "grading_input.json").write_text(json.dumps(metadata, indent=2) + "\n")


def create_iteration_directory(workspace: Path | None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    workspace_root = workspace or Path(tempfile.gettempdir()) / "pythonic-code-evals"
    iteration_dir = workspace_root.expanduser().resolve() / f"iteration-{timestamp}"
    iteration_dir.mkdir(parents=True, exist_ok=False)
    return iteration_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="all", help="all, a numeric id, or an eval name")
    parser.add_argument("--workspace", type=Path, help="directory that will contain the iteration")
    parser.add_argument("--model", default=os.environ.get("CODEX_EVAL_MODEL"))
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument("--validate", action="store_true", help="validate cases and exit")
    parser.add_argument("--dry-run", action="store_true", help="show selected trials and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cases = load_eval_cases()
        selected_cases = select_eval_cases(cases, args.case)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as error:
        print(f"Evaluation configuration error: {error}", file=sys.stderr)
        return 2

    if args.list:
        for case in cases:
            files = ", ".join(str(path) for path in case.files)
            print(f"{case.id}: {case.name} [{files}]")
        return 0
    if args.validate:
        print(f"Validated {len(cases)} pythonic-code eval cases in {EVALS_PATH}")
        return 0
    if args.dry_run:
        for case in selected_cases:
            print(f"Would run eval {case.id} ({case.name}): with_skill, without_skill")
        return 0

    codex_path = shutil.which("codex")
    if codex_path is None:
        print("codex executable was not found on PATH", file=sys.stderr)
        return 2

    try:
        iteration_dir = create_iteration_directory(args.workspace)
    except OSError as error:
        print(f"Could not create eval workspace: {error}", file=sys.stderr)
        return 2

    failures = 0
    print(f"Eval workspace: {iteration_dir}", flush=True)
    for case in selected_cases:
        write_case_metadata(iteration_dir, case)
        failures += (
            run_codex_trial(
                codex_path,
                iteration_dir,
                case,
                with_skill=True,
                model=args.model,
            )
            != 0
        )
        failures += (
            run_codex_trial(
                codex_path,
                iteration_dir,
                case,
                with_skill=False,
                model=args.model,
            )
            != 0
        )

    if failures:
        print(f"Completed with {failures} failed trial(s): {iteration_dir}", file=sys.stderr)
        return 1
    print(f"Completed paired eval runs: {iteration_dir}")
    print("Grade each pair against grading_input.json and record evidence in grading.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
