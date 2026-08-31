"""Convert xAFS personas into grouped corpora and an agent-task manifest.

Each persona is an isolated haystack, so the converter writes one corpus per
persona (``groups/<group_id>/docs``, the beam grouped convention) plus a single
``tasks.json`` whose rows name their group. Questions run under the agent-task
harness (``run agent-tasks --task-manifest``): the tool surface is the provider
axis and tokens-per-correct-answer is the headline — exactly xAFS's framing.

Files are copied byte-for-byte with their ``data/...`` relpaths preserved — no
frontmatter render. xAFS files ship without YAML frontmatter and questions cite
exact file content; BM's sync ingests ``.md`` files as notes and non-markdown
files (the ``.eml`` mails) as file resources reachable via ``read_content``
(rich surface) or ``cat``/``grep`` (posix surface), which is the required
format-spanning handling. The copy policy is "copy everything": upstream is
text-only, per-extension counts are recorded in ``conversion.json``, and the
``skipped`` list there must stay empty — a file the policy cannot place raises
instead of being silently dropped.

Anti-leakage: only each persona's ``data/`` subtree is copied. Gold answers and
prompts exist only in ``tasks.json``; a scenario/answer-key file name — or a
symlink, which could smuggle any target — appearing inside ``data/`` aborts the
conversion.

Vendor caveat: xAFS is authored by supermemory, a memory-product vendor. It is
reported as a SECONDARY dataset, never the headline, pending a question-quality
audit (docs/benchmarks.md 6d). ``sample_xafs_audit`` ships the audit tooling.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from pathlib import Path

from basic_memory_benchmarks.datasets.xafs import (
    XAFS_FAMILIES,
    XAFS_REVISION,
    XAFS_SOURCE_URL,
    XafsPersona,
    XafsQuestion,
    load_xafs,
)
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso

XAFS_CITATION = f"xAFS (supermemory, 2026), revision {XAFS_REVISION[:8]}"
XAFS_LICENSE_NOTE = (
    "CC-BY-4.0; vendor-authored (supermemory) — secondary dataset pending question-quality audit"
)

# Persona-root answer-key/scenario files must never reach an ingested corpus.
# Only data/ subtrees are copied, so hitting one of these names inside data/
# means the upstream layout changed under us — abort rather than risk leakage.
_FORBIDDEN_BASENAMES = frozenset({"question.json", "SCENARIO.md", "facts.json", "manifest.json"})

# The judge rubric mirrors the xAFS card's semantic-equivalence criteria; it is
# built deterministically so re-conversion is byte-stable.
_RUBRIC_TEMPLATE = """\
Question: {prompt}
Gold answer: {gold_answer}
Mark CORRECT iff the agent's final answer is semantically equivalent to the gold
answer: paraphrase-tolerant and format-tolerant, but exact values (numbers,
amounts, dates, identifiers) and named entities must match, and every part of a
multi-part gold answer must be covered."""


def xafs_group_id(persona_id: str) -> str:
    """Group/project key for a persona, e.g. ``dp_001`` -> ``xafs-dp001``."""
    return f"xafs-{persona_id.replace('_', '')}"


def build_judge_rubric(prompt: str, gold_answer: str) -> str:
    return _RUBRIC_TEMPLATE.format(prompt=prompt, gold_answer=gold_answer)


# --- Corrections hook (locomo-audit precedent) ---


@dataclass(frozen=True)
class XafsCorrection:
    """One audited question: a corrected gold answer, or an exclusion."""

    prompt: str  # cross-checked against the upstream prompt; drift fails loudly
    gold_answer: str | None
    excluded: bool
    reason: str | None


def load_xafs_corrections(path: Path) -> dict[str, XafsCorrection]:
    """Load corrections keyed ``"<persona_id>/<question_id>"``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"xAFS corrections file must be a JSON object: {path}")

    corrections: dict[str, XafsCorrection] = {}
    for key, raw in payload.items():
        if not isinstance(raw, dict):
            raise ValueError(f"xAFS correction {key!r} is not an object: {path}")
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"xAFS correction {key!r} needs the upstream 'prompt' text: {path}")
        excluded = bool(raw.get("excluded", False))
        gold_answer = raw.get("gold_answer")
        reason = raw.get("reason")
        if excluded:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"xAFS correction {key!r} excludes without a 'reason': {path}")
            if gold_answer is not None:
                raise ValueError(
                    f"xAFS correction {key!r} both excludes and overrides gold_answer: {path}"
                )
        elif not isinstance(gold_answer, str) or not gold_answer.strip():
            raise ValueError(
                f"xAFS correction {key!r} must override 'gold_answer' or set 'excluded': {path}"
            )
        corrections[key] = XafsCorrection(
            prompt=prompt,
            gold_answer=gold_answer if isinstance(gold_answer, str) else None,
            excluded=excluded,
            reason=reason if isinstance(reason, str) else None,
        )
    return corrections


# --- Conversion ---


def _reject_symlink(source: Path, *, persona_id: str, relpath: str) -> None:
    """Refuse to copy a symlink out of a persona tree.

    ``is_file()`` (both rglob's and the loader's) follows links, so a symlink
    named like corpus data can smuggle any target — ``question.json`` gold
    answers included — past the relpath-string checks.
    """
    if source.is_symlink():
        raise ValueError(
            f"xAFS {persona_id}: symlink would copy its target into the output: {relpath!r}"
        )


def _copy_persona_files(persona: XafsPersona, group_dir: Path) -> Counter[str]:
    """Byte-for-byte copy of the persona's data/ tree; per-extension counts."""
    docs_dir = group_dir / "docs"
    # Trigger: re-conversion into an existing output_dir (a revision bump
    # re-fetched in place — ``hf download`` never prunes removed files).
    # Why: overwrite-only copying would keep files gone upstream in the
    # ingested haystack while conversion.json counts only the new file set —
    # invisible contamination, since nothing ties the run's corpus checksum
    # back to this manifest.
    # Outcome: docs/ always mirrors exactly this conversion's inputs.
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    counts: Counter[str] = Counter()
    for relpath in persona.data_files:
        parts = Path(relpath).parts
        # Defense in depth: data_files come from rglob so they cannot escape,
        # but a symlinked or renamed upstream layout must not corrupt a corpus.
        if Path(relpath).is_absolute() or ".." in parts:
            raise ValueError(f"xAFS {persona.persona_id} file escapes the persona: {relpath!r}")
        if parts[-1] in _FORBIDDEN_BASENAMES:
            raise ValueError(
                f"xAFS {persona.persona_id}: answer-key/scenario file would land in the "
                f"ingested corpus: {relpath!r}"
            )
        # A symlink passes the relpath checks under its own name (e.g.
        # data/x.md -> ../question.json) yet copyfile would materialize its
        # target — gold answers included — into the ingested haystack.
        _reject_symlink(persona.root / relpath, persona_id=persona.persona_id, relpath=relpath)
        destination = docs_dir / relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(persona.root / relpath, destination)
        counts[Path(relpath).suffix or "(none)"] += 1
    return counts


def _persona_data_sha256(persona: XafsPersona) -> str:
    """Aggregate fingerprint over sorted (relpath, sha256(file)) pairs.

    Streams per-file digests instead of file bytes so a multi-hundred-MB
    persona never sits in memory.
    """
    digest = hashlib.sha256()
    for relpath in persona.data_files:
        digest.update(relpath.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha256_file(persona.root / relpath).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _task_row(question: XafsQuestion, group_id: str, gold_answer: str) -> dict:
    return {
        "id": f"{group_id}-{question.id}",
        "skill": question.family,
        "group": group_id,
        "source": (f"supermemory/xAFS {question.persona_id} {question.id} @{XAFS_REVISION[:8]}"),
        "prompt": question.prompt,
        "graders": [
            {"kind": "judge_rubric", "rubric": build_judge_rubric(question.prompt, gold_answer)}
        ],
        "metadata": {
            "gold_file_ids": list(question.gold_file_ids),
            "gold_answer": gold_answer,
        },
    }


def convert_xafs_to_corpus(
    *,
    dataset_root: Path,
    output_dir: Path,
    personas: Sequence[str] | None = None,
    corrections_path: Path | None = None,
) -> tuple[Path, Path, int, int]:
    """Convert selected personas into per-persona corpora + a task manifest.

    ``dataset_root`` is the local xAFS snapshot (see
    ``benchmarks/datasets/xafs/download.sh``). Returns
    ``(groups_dir, tasks_path, file_count, task_count)``. Runs pass the written
    ``conversion.json`` provenance forward by pointing ``--corpus-dir`` at
    ``groups/`` (the run manifest checksums the grouped corpus itself).
    """
    loaded = load_xafs(dataset_root, personas)
    corrections = load_xafs_corrections(corrections_path) if corrections_path is not None else {}

    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict] = []
    persona_manifests: list[dict] = []
    excluded: list[dict] = []
    corrections_applied = 0
    file_count = 0
    seen_correction_keys: set[str] = set()

    for persona in loaded:
        group_id = xafs_group_id(persona.persona_id)
        extension_counts = _copy_persona_files(persona, groups_dir / group_id)
        file_count += len(persona.data_files)

        for question in persona.questions:
            key = f"{question.persona_id}/{question.id}"
            correction = corrections.get(key)
            gold_answer = question.gold_answer
            if correction is not None:
                seen_correction_keys.add(key)
                # Trigger: the stored prompt differs from the upstream prompt.
                # Why: a silently drifted correction would override the wrong
                # question (the locomo-audit cross-check precedent).
                # Outcome: conversion aborts naming the question.
                if correction.prompt != question.prompt:
                    raise ValueError(
                        f"xAFS correction {key!r} prompt does not match the upstream "
                        "question; the dataset or corrections file drifted"
                    )
                if correction.excluded:
                    excluded.append({"question": key, "reason": correction.reason})
                    continue
                assert correction.gold_answer is not None  # enforced by the loader
                gold_answer = correction.gold_answer
                corrections_applied += 1
            tasks.append(_task_row(question, group_id, gold_answer))

        persona_manifests.append(
            {
                "persona_id": persona.persona_id,
                "group_id": group_id,
                "question_file_sha256": sha256_file(persona.root / "question.json"),
                "data_files_sha256": _persona_data_sha256(persona),
                "file_count": len(persona.data_files),
                "files_by_extension": dict(sorted(extension_counts.items())),
                "question_count": len(persona.questions),
            }
        )

    # A correction for a selected persona that matched no loaded question is
    # drift (corrections for unselected personas are expected under subsetting).
    selected_persona_ids = {persona.persona_id for persona in loaded}
    stale = sorted(
        key
        for key in corrections
        if key not in seen_correction_keys and key.split("/", 1)[0] in selected_persona_ids
    )
    if stale:
        raise ValueError(f"xAFS corrections reference unknown questions: {stale}")

    tasks_path = output_dir / "tasks.json"
    tasks_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    conversion_manifest = {
        "dataset_id": "xafs",
        "source_url": XAFS_SOURCE_URL,
        "revision": XAFS_REVISION,
        "citation": XAFS_CITATION,
        "license_note": XAFS_LICENSE_NOTE,
        "dataset_root": str(dataset_root),
        "converter": {
            "personas": list(personas) if personas is not None else None,
            "corrections_path": str(corrections_path) if corrections_path is not None else None,
        },
        "corrections": {"applied": corrections_applied, "excluded": excluded},
        # Copy policy is "copy everything"; this list existing (and staying
        # empty) is the explicit never-silently-dropped contract.
        "skipped": [],
        "file_count": file_count,
        "task_count": len(tasks),
        "personas": persona_manifests,
        "created_at_utc": utc_now_iso(),
    }
    (output_dir / "conversion.json").write_text(
        json.dumps(conversion_manifest, indent=2), encoding="utf-8"
    )

    return groups_dir, tasks_path, file_count, len(tasks)


# --- Audit sampling (the post-merge ~20-question quality audit's tooling) ---


def _stratified_allocation(counts: dict[str, int], sample_size: int) -> dict[str, int]:
    """Proportional per-family allocation via largest remainder; deterministic."""
    total = sum(counts.values())
    if sample_size >= total:
        return dict(counts)
    shares = {family: sample_size * count / total for family, count in counts.items()}
    allocation = {family: floor(share) for family, share in shares.items()}
    remainder = sample_size - sum(allocation.values())
    # Ties broken by canonical family order so the same seed reproduces exactly.
    by_fraction = sorted(
        counts,
        key=lambda family: (-(shares[family] - allocation[family]), XAFS_FAMILIES.index(family)),
    )
    for family in by_fraction:
        if remainder == 0:
            break
        if allocation[family] < counts[family]:
            allocation[family] += 1
            remainder -= 1
    return allocation


def sample_xafs_audit(
    *,
    dataset_root: Path,
    output_dir: Path,
    personas: Sequence[str] | None = None,
    sample_size: int = 20,
    seed: int = 42,
) -> tuple[Path, int]:
    """Extract a seeded, family-stratified question sample for human review.

    Writes ``audit-sample.json`` (persona, id, family, prompt, gold answer,
    gold files), verbatim copies of every gold file under
    ``sources/<persona>-<qid>/``, and a human-readable ``sample.md``. Returns
    ``(sample_path, sampled_count)``.
    """
    loaded = load_xafs(dataset_root, personas)
    by_family: dict[str, list[tuple[XafsPersona, XafsQuestion]]] = {
        family: [] for family in XAFS_FAMILIES
    }
    for persona in loaded:
        for question in persona.questions:
            by_family[question.family].append((persona, question))

    counts = {family: len(rows) for family, rows in by_family.items() if rows}
    allocation = _stratified_allocation(counts, sample_size)
    rng = random.Random(seed)
    selected: list[tuple[XafsPersona, XafsQuestion]] = []
    for family in XAFS_FAMILIES:
        rows = by_family[family]
        if rows:
            selected.extend(rng.sample(rows, allocation[family]))
    selected.sort(key=lambda pair: (pair[0].persona_id, pair[1].id))

    output_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = output_dir / "sources"
    # Trigger: re-sampling into an existing output_dir with a different
    # --n/--seed (the _copy_persona_files re-conversion precedent).
    # Why: overwrite-only copying would leave sources/<persona>-<qid>/ dirs
    # the new audit-sample.json no longer references — a reviewer would audit
    # gold files for questions that are not in the sample.
    # Outcome: sources/ always mirrors exactly this sample's questions.
    if sources_dir.exists():
        shutil.rmtree(sources_dir)
    records: list[dict] = []
    markdown_lines = [
        "# xAFS audit sample",
        "",
        f"Seed {seed}, {len(selected)} of {sum(counts.values())} questions, "
        f"stratified across families ({', '.join(f'{f}: {allocation.get(f, 0)}' for f in XAFS_FAMILIES if f in counts)}).",
        "",
        "For each question: is the prompt answerable from the gold files, and is the "
        "gold answer correct and complete? Record verdicts in "
        "benchmarks/datasets/xafs/corrections.json.",
    ]
    for persona, question in selected:
        records.append(
            {
                "persona_id": persona.persona_id,
                "id": question.id,
                "family": question.family,
                "prompt": question.prompt,
                "gold_answer": question.gold_answer,
                "gold_file_ids": list(question.gold_file_ids),
            }
        )
        source_dir = sources_dir / f"{persona.persona_id}-{question.id}"
        for relpath in question.gold_file_ids:
            # Same hole as the persona copy: a gold-file symlink would land
            # its target in the audit output rather than the named file.
            _reject_symlink(persona.root / relpath, persona_id=persona.persona_id, relpath=relpath)
            destination = source_dir / relpath
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(persona.root / relpath, destination)
        markdown_lines += [
            "",
            f"## {persona.persona_id}/{question.id} ({question.family})",
            "",
            f"**Prompt:** {question.prompt}",
            "",
            f"**Gold answer:** {question.gold_answer}",
            "",
            "**Gold files:**",
            *[
                f"- `{relpath}` (copied to `sources/{persona.persona_id}-{question.id}/{relpath}`)"
                for relpath in question.gold_file_ids
            ],
        ]

    sample_path = output_dir / "audit-sample.json"
    sample_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output_dir / "sample.md").write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")
    return sample_path, len(selected)
