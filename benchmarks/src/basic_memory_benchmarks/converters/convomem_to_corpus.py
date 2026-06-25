"""Convert sampled ConvoMem pre-mixed test cases into grouped benchmark corpora.

Sampling is stratified by (category, contextSize) with a fixed seed, and the
exact sample composition is written to ``sampling.json`` so a published number
can state precisely which slice of ConvoMem it covers.

Anti-leakage: conversations carry ``containsEvidence`` and ``model_name``
fields in the raw data; rendered docs include neither, and conversation ids
are remapped to neutral positional ids.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from basic_memory_benchmarks.datasets.convomem import load_convomem_batches

DATASET_ID = "convomem"

# Directory names -> benchmark category labels used in reports.
CATEGORY_LABELS: dict[str, str] = {
    "user_evidence": "user_facts",
    "assistant_facts_evidence": "assistant_facts",
    "changing_evidence": "knowledge_update",
    "abstention_evidence": "abstention",
    "preference_evidence": "preference",
    "implicit_connection_evidence": "implicit_connection",
}


def _render_conversation_doc(doc_id: str, messages: list[dict]) -> str:
    lines: list[str] = [
        "---",
        f"title: {doc_id}",
        "type: note",
        f"source_doc_id: {doc_id}",
        f"dataset_id: {DATASET_ID}",
        "---",
        "",
        f"# {doc_id}",
        "",
        "## Conversation",
    ]
    for message in messages:
        speaker = str(message.get("speaker", "unknown")).capitalize()
        text = str(message.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"- **{speaker}:** {' '.join(text.split())}")
    return "\n".join(lines).rstrip() + "\n"


def convert_convomem_to_corpus(
    batches_dir: Path,
    output_dir: Path,
    sample_per_stratum: int = 25,
    seed: int = 42,
    context_sizes: tuple[int, ...] | None = None,
) -> tuple[Path, Path, int, int]:
    """Sample cases per (category, contextSize) stratum and emit grouped corpora.

    Returns:
        groups_dir, queries_path, doc_count, query_count
    """
    strata: dict[tuple[str, int], list[tuple[str, int, dict]]] = {}
    for category, file_name, cases in load_convomem_batches(batches_dir):
        for case_index, case in enumerate(cases):
            context_size = int(case.get("contextSize") or 0)
            if context_sizes is not None and context_size not in context_sizes:
                continue
            strata.setdefault((category, context_size), []).append((file_name, case_index, case))

    if not strata:
        raise ValueError(
            f"No ConvoMem cases matched context sizes {context_sizes} in {batches_dir}"
        )

    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    all_queries: list[dict] = []
    sampling_manifest: dict[str, dict] = {}
    doc_count = 0

    for (category, context_size), members in sorted(strata.items()):
        sample_size = min(sample_per_stratum, len(members))
        # Sort first so sampling is deterministic regardless of load order.
        members.sort(key=lambda member: (member[0], member[1]))
        sampled = rng.sample(members, sample_size)
        label = CATEGORY_LABELS.get(category, category)
        sampling_manifest[f"{label}/cs{context_size}"] = {
            "population": len(members),
            "sampled": sample_size,
        }

        for file_name, case_index, case in sorted(sampled, key=lambda m: (m[0], m[1])):
            batch_tag = file_name.rsplit("__", 1)[-1].removesuffix(".json")
            group_id = f"{label}-cs{context_size}-{batch_tag}-{case_index:04d}"
            docs_dir = groups_dir / group_id / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)

            doc_id_by_conversation_id: dict[str, str] = {}
            for conv_index, conversation in enumerate(case.get("conversations") or []):
                doc_id = f"{group_id}-c{conv_index:03d}"
                raw_id = str(conversation.get("id") or f"conv-{conv_index}")
                doc_id_by_conversation_id[raw_id] = doc_id
                (docs_dir / f"{doc_id}.md").write_text(
                    _render_conversation_doc(doc_id, conversation.get("messages") or []),
                    encoding="utf-8",
                )
                doc_count += 1

            for query_index, evidence in enumerate(case.get("evidenceItems") or []):
                ground_truth: list[str] = []
                for evidence_conversation in evidence.get("conversations") or []:
                    raw_id = str(evidence_conversation.get("id") or "")
                    mapped = doc_id_by_conversation_id.get(raw_id)
                    # Abstention evidence references conversations that are
                    # intentionally absent from the haystack; skip those.
                    if mapped is not None:
                        ground_truth.append(mapped)

                all_queries.append(
                    {
                        "id": f"{group_id}-q{query_index}",
                        "query": str(evidence.get("question", "")).strip(),
                        "category": label,
                        "group": group_id,
                        "ground_truth": sorted(ground_truth),
                        "expected_answer": str(evidence.get("answer", "")).strip() or None,
                        "metadata": {
                            "dataset_id": DATASET_ID,
                            "context_size": context_size,
                            "abstention": label == "abstention",
                            "domain": str(evidence.get("category", "")),
                        },
                    }
                )

    queries_path = output_dir / "queries.json"
    queries_path.write_text(json.dumps(all_queries, indent=2), encoding="utf-8")

    sampling_path = output_dir / "sampling.json"
    sampling_path.write_text(
        json.dumps(
            {
                "seed": seed,
                "sample_per_stratum": sample_per_stratum,
                "context_sizes": sorted(context_sizes) if context_sizes else "all",
                "strata": sampling_manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return groups_dir, queries_path, doc_count, len(all_queries)
