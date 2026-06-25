"""Convert LongMemEval-S into grouped benchmark corpora and a query manifest.

Each LongMemEval question carries its own haystack of chat sessions, so the
output is one corpus directory per question (``groups/<question_id>/docs``)
plus a single ``queries.json`` whose entries name their group. The runner
ingests and queries each group in isolation, matching the official protocol.

Anti-leakage: the raw dataset marks evidence sessions with an ``answer_``
session-id prefix and evidence turns with ``has_answer`` flags. Session ids
are remapped to neutral positional ids and turn flags are dropped, so nothing
ingested by a provider distinguishes evidence from filler.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from basic_memory_benchmarks.datasets.longmemeval import load_longmemeval_dataset

DATASET_ID = "longmemeval_s"


def _render_session_doc(
    doc_id: str,
    session_date: str,
    turns: list[dict],
) -> str:
    lines: list[str] = [
        "---",
        f"title: {doc_id} ({session_date})",
        "type: note",
        f"source_doc_id: {doc_id}",
        f"dataset_id: {DATASET_ID}",
        f"session_date: {session_date}",
        "---",
        "",
        f"# Chat session on {session_date}",
        "",
        "## Conversation",
    ]
    for turn in turns:
        role = str(turn.get("role", "unknown")).capitalize()
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        # Keep each turn on one line so bullet-level chunking stays intact.
        lines.append(f"- **{role}:** {' '.join(content.split())}")
    return "\n".join(lines).rstrip() + "\n"


def convert_longmemeval_to_corpus(
    dataset_path: Path,
    output_dir: Path,
    max_questions: int | None = None,
    stratified: bool = False,
    seed: int = 42,
) -> tuple[Path, Path, int, int]:
    """Convert LongMemEval-S into per-question corpora + query manifest.

    ``max_questions`` with ``stratified=False`` takes the file-order prefix
    (the file is sorted by question type, so small prefixes are single-type —
    fine for smoke tests, misleading for category comparisons).
    ``stratified=True`` samples evenly across the six question types with a
    fixed seed and records the composition in sampling.json.

    Returns:
        groups_dir, queries_path, doc_count, query_count
    """
    entries = load_longmemeval_dataset(dataset_path)
    sampling_note: dict | None = None
    if max_questions is not None and stratified:
        by_type: dict[str, list[dict]] = defaultdict(list)
        for entry in entries:
            by_type[str(entry["question_type"])].append(entry)
        rng = random.Random(seed)
        per_type = max(1, max_questions // len(by_type))
        sampled: list[dict] = []
        composition: dict[str, int] = {}
        for question_type, members in sorted(by_type.items()):
            members.sort(key=lambda e: str(e["question_id"]))
            take = min(per_type, len(members))
            sampled.extend(rng.sample(members, take))
            composition[question_type] = take
        entries = sampled
        sampling_note = {
            "seed": seed,
            "max_questions": max_questions,
            "per_type": per_type,
            "composition": composition,
        }
    elif max_questions is not None:
        entries = entries[:max_questions]

    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    all_queries: list[dict] = []
    doc_count = 0

    for entry in entries:
        question_id = str(entry["question_id"])
        sessions = entry["haystack_sessions"]
        session_ids = entry["haystack_session_ids"]
        session_dates = entry["haystack_dates"]
        if not (len(sessions) == len(session_ids) == len(session_dates)):
            raise ValueError(
                f"Question {question_id}: haystack arrays misaligned "
                f"({len(sessions)} sessions, {len(session_ids)} ids, {len(session_dates)} dates)"
            )

        docs_dir = groups_dir / question_id / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Neutral positional doc ids; the raw ids leak evidence via the
        # "answer_" prefix. A few haystacks repeat a session id — keep the
        # first occurrence so each session is ingested once.
        doc_id_by_session_id: dict[str, str] = {}
        for index, (session, session_id, session_date) in enumerate(
            zip(sessions, session_ids, session_dates)
        ):
            if str(session_id) in doc_id_by_session_id:
                continue
            doc_id = f"{question_id}-s{index:03d}"
            doc_id_by_session_id[str(session_id)] = doc_id
            doc_path = docs_dir / f"{doc_id}.md"
            doc_path.write_text(
                _render_session_doc(doc_id, str(session_date), session),
                encoding="utf-8",
            )
            doc_count += 1

        ground_truth: list[str] = []
        for answer_session_id in entry["answer_session_ids"]:
            mapped = doc_id_by_session_id.get(str(answer_session_id))
            if mapped is None:
                raise ValueError(
                    f"Question {question_id}: answer session {answer_session_id!r} "
                    "not present in haystack"
                )
            ground_truth.append(mapped)

        is_abstention = question_id.endswith("_abs")
        all_queries.append(
            {
                "id": question_id,
                "query": str(entry["question"]).strip(),
                "category": str(entry["question_type"]),
                "group": question_id,
                "ground_truth": sorted(ground_truth),
                "expected_answer": str(entry["answer"]).strip(),
                "metadata": {
                    "dataset_id": DATASET_ID,
                    "question_date": str(entry["question_date"]),
                    "abstention": is_abstention,
                },
            }
        )

    queries_path = output_dir / "queries.json"
    queries_path.write_text(json.dumps(all_queries, indent=2), encoding="utf-8")
    if sampling_note is not None:
        (output_dir / "sampling.json").write_text(
            json.dumps(sampling_note, indent=2), encoding="utf-8"
        )

    return groups_dir, queries_path, doc_count, len(all_queries)
