"""Convert BEAM conversations into grouped benchmark corpora and a query manifest.

BEAM is per-conversation-haystack: each conversation is a distinct persona whose
probing questions are answered against that conversation alone upstream —
exactly the LongMemEval grouped shape. The converter writes one corpus per
conversation (``groups/<group_id>/docs``) plus a single ``queries.json`` whose
entries name their group, so the existing grouped runner ingests and queries
each conversation in isolation with zero orchestration changes.

Ingestion-mode seam: loading (``datasets.beam``) is mode-independent; rendering
is a function from a ``BeamConversation`` to docs plus a message-id→doc-id map
(``RenderedGroup``). Ground truth is derived from that map, never from raw
layout assumptions, so a future agent-curated renderer can return its own
mapping and queries/ground truth/scoring stay valid unchanged. v1 ships only
the ``raw`` transcript-as-notes mode.

Anti-leakage: upstream message content carries a trailing ``->-> <b>,<q>``
index marker (``<q>`` is sometimes non-numeric, e.g. ``N/A``) linking
transcript text to probe indices; it is stripped from rendered docs, and
conversion fails fast if any marker survives the strip. Rubrics and reference
answers exist only in ``queries.json`` and are never ingested.

Provenance: ``RunConfig.dataset_path`` must be a checksummable file, but the
BEAM source is a directory tree. The converter therefore writes
``<output_dir>/conversion.json`` recording the tier, source root, per-file
sha256 of every chat/probing file consumed, and converter options — BEAM runs
pass that manifest as ``--dataset-path`` so the run checksum pins the exact
converted inputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from basic_memory_benchmarks.datasets.beam import BeamConversation, BeamMessage, load_beam_tier
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso

BEAM_SOURCE_URL = "https://github.com/mohammadtavakoli78/BEAM"
BEAM_CITATION = "BEAM: Beyond a Million Tokens (ICLR 2026, arXiv 2510.27246)"
BEAM_LICENSE_NOTE = "Code MIT; benchmark data CC BY-SA 4.0"

# Trailing probe-index marker in message content: "->->" followed by a
# comma-separated id list. A survey of every marker in the 100K tier
# (2,199 total) found exactly five shapes: " 1,1" (2190), " 1,5)" (6),
# " 2,N/A" (1), " 2,22, 24" (1), and a double-space variant (1). The
# first id is always an int; later ids are ints or N/A, spaces around
# commas optional; the trailing ")" is generator junk (every such message
# contains zero opening parens), so it strips with the marker. Kept this
# narrow deliberately — anything else containing "->->" trips the
# fail-fast below instead of silently stripping content. The marker links
# transcript text back to probe indices, so it must never be ingested.
_INDEX_MARKER_PATTERN = re.compile(r"\s*->->\s*\d+(?:\s*,\s*(?:\d+|N/A))*\)?\s*$")


@dataclass(frozen=True)
class RenderedGroup:
    """Result of rendering one conversation into a group corpus."""

    doc_count: int
    doc_id_for_message: dict[int, str]  # global message id -> owning source_doc_id


def _clean_message_content(content: str) -> str:
    # Strip the leakage marker first, then collapse whitespace so each turn
    # stays on one line and bullet-level chunking stays intact.
    stripped = _INDEX_MARKER_PATTERN.sub("", content)
    # Trigger: "->->" survives the strip (a marker variant the pattern does
    # not know, or a marker that is not trailing).
    # Why: a surviving marker leaks probe indices into ingested docs and
    # silently corrupts the benchmark's anti-leakage guarantee.
    # Outcome: conversion aborts so the pattern is widened deliberately.
    if "->->" in stripped:
        raise ValueError(
            f"BEAM probe-index marker survived cleaning (unhandled variant): {content[-120:]!r}"
        )
    return " ".join(stripped.split())


def _batch_anchor(batch_messages: list[BeamMessage], batch_time_anchor: str | None) -> str | None:
    if batch_time_anchor:
        return batch_time_anchor
    for message in batch_messages:
        if message.time_anchor:
            return message.time_anchor
    return None


def render_raw_group(
    conversation: BeamConversation, group_id: str, group_dir: Path, dataset_id: str
) -> RenderedGroup:
    """Render one conversation as raw transcript notes: one doc per session."""
    docs_dir = group_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    doc_id_for_message: dict[int, str] = {}
    doc_count = 0
    # The time anchor is carried forward as "current anchor" until the next
    # one appears: without an anchor in the doc, temporal probes ("how long
    # after March 15...") are unanswerable by any provider — same rationale
    # as the LoCoMo session_date handling.
    current_anchor: str | None = None

    for batch in conversation.batches:
        batch_messages = [message for session in batch.turns for message in session]
        anchor = _batch_anchor(batch_messages, batch.time_anchor)
        if anchor:
            current_anchor = anchor

        for session_index, session in enumerate(batch.turns):
            doc_id = f"{group_id}-b{batch.batch_number:02d}-s{session_index:03d}"
            date_suffix = f" ({current_anchor})" if current_anchor else ""

            lines: list[str] = [
                "---",
                f"title: {doc_id}{date_suffix}",
                "type: note",
                f"source_doc_id: {doc_id}",
                f"dataset_id: {dataset_id}",
                f"conversation_id: {group_id}",
            ]
            if current_anchor:
                lines.append(f"session_date: {current_anchor}")
            lines += [
                "---",
                "",
                f"# Chat session ({current_anchor})" if current_anchor else f"# {doc_id}",
                "",
                "## Conversation",
            ]
            for message in session:
                doc_id_for_message[message.id] = doc_id
                content = _clean_message_content(message.content)
                if not content:
                    continue
                speaker = "User" if message.role == "user" else "Assistant"
                lines.append(f"- **{speaker}:** {content}")

            (docs_dir / f"{doc_id}.md").write_text(
                "\n".join(lines).rstrip() + "\n", encoding="utf-8"
            )
            doc_count += 1

    return RenderedGroup(doc_count=doc_count, doc_id_for_message=doc_id_for_message)


def _probe_queries(
    conversation: BeamConversation,
    group_id: str,
    dataset_id: str,
    rendered: RenderedGroup,
) -> list[dict]:
    queries: list[dict] = []
    for probe in conversation.probes:
        ground_truth: set[str] = set()
        for chat_id in probe.source_chat_ids:
            doc_id = rendered.doc_id_for_message.get(chat_id)
            if doc_id is None:
                raise ValueError(
                    f"BEAM conversation {conversation.conversation_id}: probe "
                    f"{probe.ability}[{probe.index}] cites chat id {chat_id} "
                    "not present in the chat"
                )
            ground_truth.add(doc_id)

        # Abstention probes get empty ground_truth (diagnose keys
        # "unanswerable" off that) and their ideal_response gold ("...there is
        # no information related to...") triggers the binary QA judge's
        # unanswerable branch — abstention grading works with no QA changes.
        queries.append(
            {
                "id": f"{group_id}-{probe.ability}-{probe.index}",
                "query": probe.question,
                # category feeds every existing by_category breakdown.
                "category": probe.ability,
                "group": group_id,
                "ground_truth": sorted(ground_truth),
                "expected_answer": probe.reference_answer,
                "metadata": {
                    "dataset_id": dataset_id,
                    "tier": conversation.tier,
                    "ability": probe.ability,
                    "probe_index": probe.index,
                    "difficulty": probe.difficulty,
                    # The nugget list rides query metadata into the retrieval
                    # rows, where the beam-score stage picks it up.
                    "rubric": probe.rubric,
                    "abstention": probe.ability == "abstention",
                },
            }
        )
    return queries


def convert_beam_to_corpus(
    *,
    dataset_root: Path,
    output_dir: Path,
    tier: str,
    max_conversations: int | None = None,
    mode: str = "raw",
) -> tuple[Path, Path, int, int]:
    """Convert one BEAM tier into per-conversation corpora + query manifest.

    ``dataset_root`` is the upstream ``chats/`` directory of a local BEAM
    checkout. Returns ``(groups_dir, queries_path, doc_count, query_count)``.
    """
    if mode != "raw":
        raise ValueError(
            f"Unsupported BEAM ingestion mode {mode!r}; v1 supports only 'raw' "
            "(agent-curated ingestion is a future renderer behind the same seam)"
        )

    conversations = load_beam_tier(dataset_root, tier, max_conversations=max_conversations)
    dataset_id = f"beam-{tier.lower()}"

    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    all_queries: list[dict] = []
    doc_count = 0
    conversation_manifests: list[dict] = []

    for conversation in conversations:
        group_id = f"{dataset_id}-c{int(conversation.conversation_id):02d}"
        rendered = render_raw_group(conversation, group_id, groups_dir / group_id, dataset_id)
        doc_count += rendered.doc_count
        all_queries.extend(_probe_queries(conversation, group_id, dataset_id, rendered))

        conv_dir = dataset_root / tier / conversation.conversation_id
        conversation_manifests.append(
            {
                "conversation_id": conversation.conversation_id,
                "group_id": group_id,
                "chat_file": conversation.source_chat_file,
                "chat_sha256": sha256_file(conv_dir / conversation.source_chat_file),
                "probing_file": "probing_questions/probing_questions.json",
                "probing_sha256": sha256_file(
                    conv_dir / "probing_questions" / "probing_questions.json"
                ),
            }
        )

    queries_path = output_dir / "queries.json"
    queries_path.write_text(json.dumps(all_queries, indent=2), encoding="utf-8")

    conversion_manifest = {
        "dataset_id": dataset_id,
        "tier": tier,
        "source_url": BEAM_SOURCE_URL,
        "citation": BEAM_CITATION,
        "license_note": BEAM_LICENSE_NOTE,
        "dataset_root": str(dataset_root),
        "converter": {"mode": mode, "max_conversations": max_conversations},
        "created_at_utc": utc_now_iso(),
        "conversations": conversation_manifests,
    }
    (output_dir / "conversion.json").write_text(
        json.dumps(conversion_manifest, indent=2), encoding="utf-8"
    )

    return groups_dir, queries_path, doc_count, len(all_queries)
