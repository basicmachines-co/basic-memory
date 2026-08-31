"""Dataset-driven agent-task source: a ``tasks.json`` manifest -> AgentTaskSpec.

The shipped tasks in ``tasks.py`` are hand-written Python data; converted
datasets (``convert xafs``) emit a JSON manifest instead. This module parses
that manifest into the same ``AgentTaskSpec`` shape the driver consumes, so
budgets, fairness, artifacts, and reporting are identical for shipped and
dataset-driven tasks — the seam beside ``tasks.select_tasks``.

v1 manifest grader kinds are ``judge_rubric`` and ``tool_called`` only:
manifest tasks run against shared read-only group projects, where
project-state graders would cross-contaminate. An unknown kind fails fast
rather than silently skipping a grader.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    Grader,
    JudgeRubric,
    ToolCalled,
)

_REQUIRED_TASK_KEYS = ("id", "skill", "group", "source", "prompt", "graders")


def _manifest_string(raw: dict, key: str, *, label: str, path: Path) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Task manifest {label} has an empty or non-string {key!r}: {path}")
    return value


def _parse_grader(raw: object, *, label: str, path: Path) -> Grader:
    if not isinstance(raw, dict):
        raise ValueError(f"Task manifest {label} grader is not an object: {path}")
    kind = raw.get("kind")
    if kind == "judge_rubric":
        rubric = raw.get("rubric")
        if not isinstance(rubric, str) or not rubric.strip():
            raise ValueError(f"Task manifest {label} judge_rubric has no rubric text: {path}")
        return JudgeRubric(rubric=rubric)
    if kind == "tool_called":
        pattern = raw.get("name_pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"Task manifest {label} tool_called has no name_pattern: {path}")
        return ToolCalled(name_pattern=pattern)
    raise ValueError(
        f"Task manifest {label} has unknown grader kind {kind!r}: {path}. "
        "v1 manifest kinds: judge_rubric, tool_called"
    )


def _parse_task(raw: object, *, position: int, path: Path) -> AgentTaskSpec:
    label = f"task at index {position}"
    if not isinstance(raw, dict):
        raise ValueError(f"Task manifest {label} is not an object: {path}")
    missing = [key for key in _REQUIRED_TASK_KEYS if key not in raw]
    if missing:
        raise ValueError(f"Task manifest {label} is missing keys {missing}: {path}")
    graders_raw = raw["graders"]
    if not isinstance(graders_raw, list) or not graders_raw:
        raise ValueError(f"Task manifest {label} has an empty or non-list 'graders': {path}")
    return AgentTaskSpec(
        id=_manifest_string(raw, "id", label=label, path=path),
        skill=_manifest_string(raw, "skill", label=label, path=path),
        source=_manifest_string(raw, "source", label=label, path=path),
        prompt=_manifest_string(raw, "prompt", label=label, path=path),
        graders=tuple(_parse_grader(grader, label=label, path=path) for grader in graders_raw),
        group=_manifest_string(raw, "group", label=label, path=path),
    )


def load_task_manifest(path: Path, task_ids: Sequence[str] | None = None) -> list[AgentTaskSpec]:
    """Load a converted-dataset task manifest, optionally filtered by id.

    Unknown filter ids are rejected loudly (mirroring ``select_tasks``); the
    returned tasks are ordered ``(group, id)`` so the driver ingests each group
    corpus once and runs its tasks contiguously.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"Task manifest must be a non-empty JSON array: {path}")

    specs: list[AgentTaskSpec] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(payload):
        spec = _parse_task(raw, position=position, path=path)
        if spec.id in seen_ids:
            raise ValueError(f"Task manifest has duplicate task id {spec.id!r}: {path}")
        seen_ids.add(spec.id)
        specs.append(spec)

    if task_ids:
        unknown = [task_id for task_id in task_ids if task_id not in seen_ids]
        if unknown:
            raise ValueError(f"Unknown task ids: {unknown}. Known: {sorted(seen_ids)}")
        unique_ids = set(dict.fromkeys(task_ids))
        specs = [spec for spec in specs if spec.id in unique_ids]

    return sorted(specs, key=lambda spec: (spec.group or "", spec.id))
