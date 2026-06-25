"""Convert LoCoMo dataset into deterministic benchmark corpus and query files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from basic_memory_benchmarks.datasets.locomo import load_locomo_dataset


CATEGORY_MAP: dict[int, str] = {
    1: "single_hop",
    2: "multi_hop",
    3: "temporal",
    4: "open_domain",
    5: "adversarial",
}


def _session_num_from_evidence(evidence_id: str) -> int | None:
    match = re.match(r"^D(\d+):", evidence_id)
    if not match:
        return None
    return int(match.group(1))


def _sorted_session_keys(conversation_blob: dict) -> list[str]:
    keys = [
        k
        for k, v in conversation_blob.items()
        if re.match(r"^session_\d+$", k) and isinstance(v, list)
    ]
    return sorted(keys, key=lambda key: int(key.split("_")[1]))


def convert_locomo_to_corpus(
    dataset_path: Path,
    output_dir: Path,
    max_conversations: int | None = None,
    audit_corrections_path: Path | None = None,
) -> tuple[Path, Path, int, int]:
    """Convert LoCoMo into markdown docs + query manifest.

    When ``audit_corrections_path`` is given, the Penfield audit's corrected
    answers and evidence citations replace the originals for the 156 known
    answer-key errors. Each corrected query is cross-checked by question text
    so audit/dataset drift fails loudly.

    Returns:
        docs_dir, queries_path, doc_count, query_count
    """
    corrections: dict[str, dict] = {}
    if audit_corrections_path is not None:
        from basic_memory_benchmarks.datasets.locomo_audit import load_locomo_corrections

        corrections = load_locomo_corrections(audit_corrections_path)

    conversations = load_locomo_dataset(dataset_path)
    # Prefix slicing keeps conv_index aligned with the audit's
    # locomo_<conv>_qa<index> ids.
    if max_conversations is not None:
        conversations = conversations[:max_conversations]

    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    all_queries: list[dict] = []
    doc_count = 0

    for conv_index, conversation in enumerate(conversations):
        blob = conversation.get("conversation", {})
        session_keys = _sorted_session_keys(blob)
        conv_id = f"locomo-c{conv_index:02d}"
        session_doc_id: dict[int, str] = {}

        for session_key in session_keys:
            session_num = int(session_key.split("_")[1])
            doc_id = f"{conv_id}-s{session_num:02d}"
            session_doc_id[session_num] = doc_id
            turns = blob.get(session_key, [])
            # The session timestamp is the anchor for every relative time
            # expression in the dialogue ("yesterday", "last Saturday").
            # Without it in the doc, date questions are unanswerable by any
            # provider — LoCoMo multi_hop/temporal collapse to abstention.
            session_date = str(blob.get(f"{session_key}_date_time", "")).strip()
            date_suffix = f" ({session_date})" if session_date else ""

            lines: list[str] = [
                "---",
                f"title: {doc_id}{date_suffix}",
                "type: note",
                f"source_doc_id: {doc_id}",
                "dataset_id: locomo",
                f"conversation_id: {conv_id}",
                f"session_number: {session_num}",
            ]
            if session_date:
                lines.append(f"session_date: {session_date}")
            lines += [
                "---",
                "",
                f"# Chat session at {session_date}" if session_date else f"# {doc_id}",
                "",
                "## Conversation",
            ]
            for turn in turns:
                speaker = str(turn.get("speaker", "unknown"))
                text = str(turn.get("text", "")).strip()
                if not text:
                    continue
                lines.append(f"- **{speaker}:** {text}")

            doc_path = docs_dir / f"{doc_id}.md"
            doc_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            doc_count += 1

        for query_index, qa in enumerate(conversation.get("qa", [])):
            category_id = int(qa.get("category", 0))
            category = CATEGORY_MAP.get(category_id, f"cat_{category_id}")

            answer = qa.get("answer") or qa.get("adversarial_answer")
            evidence = qa.get("evidence") or []
            metadata: dict = {
                "dataset_id": "locomo",
                "conversation_id": conv_id,
                "adversarial": category_id == 5,
            }

            correction = corrections.get(f"locomo_{conv_index}_qa{query_index}")
            if correction is not None:
                audit_question = str(correction.get("question", "")).strip()
                actual_question = str(qa.get("question", "")).strip()
                if audit_question != actual_question:
                    raise ValueError(
                        f"Audit correction locomo_{conv_index}_qa{query_index} does not "
                        f"match dataset question: {audit_question!r} != {actual_question!r}"
                    )
                answer = correction["correct_answer"]
                if correction.get("correct_evidence"):
                    evidence = correction["correct_evidence"]
                metadata["audit_corrected"] = True
                metadata["audit_error_type"] = str(correction["error_type"])

            ground_truth_docs: set[str] = set()
            for evidence_id in evidence:
                if not isinstance(evidence_id, str):
                    continue
                session_num = _session_num_from_evidence(evidence_id)
                if session_num is None:
                    continue
                if session_num in session_doc_id:
                    ground_truth_docs.add(session_doc_id[session_num])

            all_queries.append(
                {
                    "id": f"{conv_id}-q{query_index:04d}",
                    "query": str(qa.get("question", "")).strip(),
                    "category": category,
                    "category_id": category_id,
                    "ground_truth": sorted(ground_truth_docs),
                    "expected_answer": str(answer).strip() if answer else None,
                    "metadata": metadata,
                }
            )

    queries_path = output_dir / "queries.json"
    queries_path.parent.mkdir(parents=True, exist_ok=True)
    queries_path.write_text(json.dumps(all_queries, indent=2), encoding="utf-8")

    return docs_dir, queries_path, doc_count, len(all_queries)
