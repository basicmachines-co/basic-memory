"""xAFS dataset loading.

xAFS (supermemory's "Agentic File System" benchmark,
https://huggingface.co/datasets/supermemory/xAFS, CC-BY-4.0) ships 13 synthetic
personas (``dp_001``..``dp_013``), each a file tree under ``data/`` plus a
``question.json`` of single-hop / multi-hop / format-spanning questions with
gold answers and gold source files. Corpus size scales from 5 to ~10K files per
persona (~19K files / ~837MB total), which is why persona subsetting is a
first-class loading option.

The dataset is never vendored into this repo — see
``benchmarks/datasets/xafs/download.sh`` for the pinned ``hf download`` fetch.
This module only loads a local snapshot; there is no fetch code here.

Count note: the upstream README advertises 110 questions (35/50/25 per family)
but the shipped ``question.json`` files at the pinned revision sum to 33/51/26.
The loader trusts the JSON and asserts nothing about totals.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

XAFS_SOURCE_URL = "https://huggingface.co/datasets/supermemory/xAFS"
# Pinned dataset revision. Bump deliberately (and re-verify counts/layout).
XAFS_REVISION = "21142b2c01113cb881c80d6c99bcf0f412ed17f2"

# Exact upstream family keys; the harness maps family -> task "skill" so the
# existing per-skill reporting is the per-question-type breakdown.
XAFS_FAMILIES = ("single_hop", "multi_hop", "format_spanning")

_PERSONA_DIR_PATTERN = re.compile(r"^dp_\d{3}$")
_REQUIRED_QUESTION_KEYS = ("id", "family", "prompt", "gold_file_ids", "gold_answer")


@dataclass(frozen=True)
class XafsQuestion:
    id: str  # upstream "id" ("q01"...); unique only within a persona
    persona_id: str  # "dp_001".."dp_013" (the persona dir name, added by the loader)
    family: str  # one of XAFS_FAMILIES
    prompt: str
    gold_file_ids: tuple[str, ...]  # verbatim upstream "data/..." relpaths
    gold_answer: str
    extras: Mapping[str, Any]  # unknown upstream keys, preserved verbatim


@dataclass(frozen=True)
class XafsPersona:
    persona_id: str
    root: Path  # the local persona directory
    # Sorted relpaths from the persona root, every extension, all prefixed
    # "data/" — the same path vocabulary gold_file_ids uses, so gold references
    # and corpus files never need a mapping layer.
    data_files: tuple[str, ...]
    questions: tuple[XafsQuestion, ...]  # upstream array order preserved


def _parse_gold_file_ids(raw: object, *, persona_dir: Path, question_label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"xAFS {question_label} has an empty or non-list gold_file_ids")
    gold: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"xAFS {question_label} gold_file_ids must be non-empty strings")
        relpath = item.strip()
        # Gold references must stay inside the persona's data/ subtree: an
        # absolute or escaping path would let ground truth point outside the
        # corpus the converter copies (and outside the anti-leakage boundary).
        if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
            raise ValueError(f"xAFS {question_label} gold file id escapes the persona: {relpath!r}")
        if not relpath.startswith("data/"):
            raise ValueError(
                f"xAFS {question_label} gold file id is not data/-prefixed: {relpath!r}"
            )
        # Existence guards truncated downloads: every gold reference at the
        # pinned revision resolves, so a miss means a broken local snapshot.
        if not (persona_dir / relpath).is_file():
            raise ValueError(
                f"xAFS {question_label} gold file missing on disk: {persona_dir / relpath}"
                " (partial download? re-run benchmarks/datasets/xafs/download.sh)"
            )
        gold.append(relpath)
    return tuple(gold)


def _required_string(raw: dict[str, Any], key: str, *, question_label: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"xAFS {question_label} has an empty or non-string {key!r}")
    return value


def _parse_question(
    raw: object, *, persona_id: str, persona_dir: Path, position: int
) -> XafsQuestion:
    label = f"question at index {position} in {persona_dir / 'question.json'}"
    if not isinstance(raw, dict):
        raise ValueError(f"xAFS {label} is not an object: {raw!r}")
    missing = [key for key in _REQUIRED_QUESTION_KEYS if key not in raw]
    if missing:
        raise ValueError(f"xAFS {label} is missing keys {missing}")
    question_id = _required_string(raw, "id", question_label=label)
    family = _required_string(raw, "family", question_label=label)
    if family not in XAFS_FAMILIES:
        raise ValueError(
            f"xAFS {label} has unknown family {family!r}; expected one of {XAFS_FAMILIES}"
        )
    return XafsQuestion(
        id=question_id,
        persona_id=persona_id,
        family=family,
        prompt=_required_string(raw, "prompt", question_label=label),
        gold_file_ids=_parse_gold_file_ids(
            raw["gold_file_ids"], persona_dir=persona_dir, question_label=label
        ),
        gold_answer=_required_string(raw, "gold_answer", question_label=label),
        extras={key: value for key, value in raw.items() if key not in _REQUIRED_QUESTION_KEYS},
    )


def load_xafs_persona(persona_dir: Path) -> XafsPersona:
    """Load one persona directory (``data/`` tree + ``question.json``)."""
    persona_id = persona_dir.name
    question_path = persona_dir / "question.json"
    if not question_path.is_file():
        raise FileNotFoundError(f"xAFS persona has no question.json: {persona_dir}")
    data_dir = persona_dir / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"xAFS persona has no data/ directory: {persona_dir}")
    data_files = tuple(
        sorted(str(path.relative_to(persona_dir)) for path in data_dir.rglob("*") if path.is_file())
    )
    if not data_files:
        raise ValueError(f"xAFS persona data/ directory is empty: {data_dir}")

    payload = json.loads(question_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"xAFS question.json must be a non-empty JSON array: {question_path}")

    questions: list[XafsQuestion] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(payload):
        question = _parse_question(
            raw, persona_id=persona_id, persona_dir=persona_dir, position=position
        )
        # (persona_id, id) is the upstream identity; a duplicate id inside one
        # persona would silently collapse tasks and corrections.
        if question.id in seen_ids:
            raise ValueError(f"xAFS duplicate question id {question.id!r} in {question_path}")
        seen_ids.add(question.id)
        questions.append(question)

    return XafsPersona(
        persona_id=persona_id,
        root=persona_dir,
        data_files=data_files,
        questions=tuple(questions),
    )


def load_xafs(root: Path, personas: Sequence[str] | None = None) -> list[XafsPersona]:
    """Load personas from a local xAFS snapshot.

    ``personas`` is an explicit subset of persona ids (full ingestion is
    ~837MB, so subsetting is normal); ``None`` loads every ``dp_NNN`` directory
    present — which supports partial ``--include`` downloads. Unknown requested
    ids fail fast listing what is actually on disk.
    """
    if not root.is_dir():
        raise FileNotFoundError(
            f"xAFS dataset root not found: {root} (fetch with benchmarks/datasets/xafs/download.sh)"
        )
    available = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _PERSONA_DIR_PATTERN.fullmatch(entry.name)
    )
    if not available:
        raise ValueError(f"No xAFS persona directories (dp_NNN) under {root}")

    if personas is None:
        selected = available
    else:
        requested = list(dict.fromkeys(personas))
        unknown = [persona for persona in requested if persona not in available]
        if unknown:
            raise ValueError(
                f"Unknown xAFS personas {unknown}; available under {root}: {available}"
            )
        selected = sorted(requested)

    return [load_xafs_persona(root / persona_id) for persona_id in selected]
