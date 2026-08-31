"""BEAM dataset loading.

BEAM ("Beyond a Million Tokens", arXiv 2510.27246, ICLR 2026;
https://github.com/mohammadtavakoli78/BEAM) probes long-term conversational
memory across ten abilities over synthetic multi-month chat histories. The
upstream repo ships the data in-tree as ``chats/{100K,500K,1M,10M}/<N>/`` where
each numeric directory is one conversation: a ``chat.json`` (list of batches,
each batch a list of chat sessions) plus
``probing_questions/probing_questions.json`` (dict keyed by ability).

The dataset is never vendored into this repo — see
``benchmarks/datasets/beam/download.sh`` for the sparse-clone fetch. This
module only loads a local checkout; there is no fetch code here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 10M is deliberately absent: that tier uses the combined "plan-N" layout
# (top-level plan directories, differently shaped chat.json) rather than the
# per-conversation layout below, and is out of scope for v1.
BEAM_TIERS = ("100K", "500K", "1M")

# Exact upstream ability keys (see BEAM src/evaluation/report_results.py).
# Note: the issue text's "Multi-hop Reasoning" is upstream's
# multi_session_reasoning.
ABILITY_KEYS = (
    "abstention",
    "contradiction_resolution",
    "event_ordering",
    "information_extraction",
    "instruction_following",
    "knowledge_update",
    "multi_session_reasoning",
    "preference_following",
    "summarization",
    "temporal_reasoning",
)

# The reference-answer field name varies per ability upstream — mirror exactly.
REFERENCE_ANSWER_FIELDS: dict[str, str] = {
    "abstention": "ideal_response",
    "contradiction_resolution": "ideal_answer",
    "event_ordering": "answer",
    "information_extraction": "answer",
    "instruction_following": "expected_compliance",
    "knowledge_update": "answer",
    "multi_session_reasoning": "answer",
    "preference_following": "expected_compliance",
    "summarization": "ideal_summary",
    "temporal_reasoning": "answer",
}


@dataclass(frozen=True)
class BeamMessage:
    role: str  # "user" | "assistant"
    id: int  # global per conversation, 0-based, interleaved across batches
    content: str
    question_type: str | None  # "main_question" | "answer_ai_question" | "followup_question" | None
    time_anchor: str | None  # e.g. "March-15-2024"; sparse (usually one per batch)
    index: str | None  # "batch,question"; also echoed in content as a trailing "->-> b,q" marker


@dataclass(frozen=True)
class BeamBatch:
    batch_number: int
    time_anchor: str | None
    turns: list[list[BeamMessage]]  # sessions; each session is a list of messages


@dataclass(frozen=True)
class BeamProbe:
    ability: str  # one of ABILITY_KEYS
    index: int  # position within the ability list — the upstream identity key
    question: str
    difficulty: str  # passed through verbatim (upstream has a stray "clear")
    rubric: list[str]  # THE nugget list; for event_ordering: the ordered event labels
    reference_answer: str  # resolved via REFERENCE_ANSWER_FIELDS
    source_chat_ids: list[int]  # normalized flat sorted ids; [] for abstention
    extras: dict[str, Any]  # remaining upstream fields verbatim (abstention_type, ...)


@dataclass(frozen=True)
class BeamConversation:
    conversation_id: str  # the bare integer dir name, e.g. "1"
    tier: str
    source_chat_file: str  # "chat_trunecated.json" or "chat.json" (which one was loaded)
    batches: list[BeamBatch]
    probes: list[BeamProbe]  # flattened, ability-major, upstream order preserved


def _parse_message(raw: object, conv_dir: Path) -> BeamMessage:
    if not isinstance(raw, dict):
        raise ValueError(f"BEAM chat message is not an object in {conv_dir}: {raw!r}")
    for key in ("role", "id", "content"):
        if key not in raw:
            raise ValueError(f"BEAM chat message missing {key!r} in {conv_dir}: {raw!r}")
    question_type = raw.get("question_type")
    time_anchor = raw.get("time_anchor")
    index = raw.get("index")
    return BeamMessage(
        role=str(raw["role"]),
        id=int(raw["id"]),
        content=str(raw["content"]),
        question_type=str(question_type) if question_type is not None else None,
        time_anchor=str(time_anchor) if time_anchor is not None else None,
        index=str(index) if index is not None else None,
    )


def _parse_batch(raw: object, conv_dir: Path) -> BeamBatch:
    # Trigger: a chat entry without batch_number/turns.
    # Why: the 10M tier's combined "plan-N" files are lists of single-key
    # plan objects, not batches — silently mis-parsing them would produce a
    # corrupt corpus.
    # Outcome: fail fast, naming the unsupported layout.
    if not isinstance(raw, dict) or "batch_number" not in raw or "turns" not in raw:
        raise ValueError(
            f"BEAM chat entry in {conv_dir} is not a batch object with "
            "'batch_number' and 'turns'. The 10M tier uses the plan-N combined "
            "layout, which is not supported in v1."
        )
    turns = raw["turns"]
    if not isinstance(turns, list) or not all(isinstance(session, list) for session in turns):
        raise ValueError(f"BEAM batch 'turns' must be a list of sessions in {conv_dir}")
    time_anchor = raw.get("time_anchor")
    return BeamBatch(
        batch_number=int(raw["batch_number"]),
        time_anchor=str(time_anchor) if time_anchor is not None else None,
        turns=[[_parse_message(message, conv_dir) for message in session] for session in turns],
    )


def _normalize_source_chat_ids(value: object, *, ability: str, conv_dir: Path) -> list[int]:
    """Normalize the per-probe evidence ids to a flat sorted list.

    Usually a list of ints, but knowledge_update ships a dict (e.g.
    ``{"original_info": [86], "updated_info": [114]}``), abstention omits
    the field entirely, and event_ordering mixes ints with one level of
    int-list groups when a single event's evidence spans chats (observed in
    the live 100K tier: ``[116, 118, ..., [136, 138]]``) — normalize to the
    flattened union / empty list.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        merged: list[object] = []
        for group in value.values():
            if not isinstance(group, list):
                raise ValueError(
                    f"BEAM {ability} source_chat_ids dict values must be lists in {conv_dir}"
                )
            merged.extend(group)
    elif isinstance(value, list):
        merged = list(value)
    else:
        raise ValueError(
            f"BEAM {ability} source_chat_ids must be a list or dict in {conv_dir}: {value!r}"
        )
    chat_ids: set[int] = set()
    for item in merged:
        # One level of nesting only: an int-list group is an event whose
        # evidence spans chats; anything deeper or non-int still fails fast.
        group_items = item if isinstance(item, list) else [item]
        for chat_id in group_items:
            if not isinstance(chat_id, int):
                raise ValueError(
                    f"BEAM {ability} source_chat_ids must contain ints or int lists "
                    f"in {conv_dir}: {item!r}"
                )
            chat_ids.add(chat_id)
    return sorted(chat_ids)


def _parse_probe(raw: object, *, ability: str, index: int, conv_dir: Path) -> BeamProbe:
    if not isinstance(raw, dict):
        raise ValueError(f"BEAM {ability} probe {index} is not an object in {conv_dir}")
    question = str(raw.get("question") or "").strip()
    if not question:
        raise ValueError(f"BEAM {ability} probe {index} has no question in {conv_dir}")
    rubric = raw.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        raise ValueError(f"BEAM {ability} probe {index} has no rubric list in {conv_dir}")
    reference_field = REFERENCE_ANSWER_FIELDS[ability]
    reference_answer = str(raw.get(reference_field) or "").strip()
    if not reference_answer:
        raise ValueError(
            f"BEAM {ability} probe {index} missing reference answer field "
            f"{reference_field!r} in {conv_dir}"
        )
    consumed = {"question", "rubric", "difficulty", "source_chat_ids", reference_field}
    return BeamProbe(
        ability=ability,
        index=index,
        question=question,
        difficulty=str(raw.get("difficulty") or ""),
        rubric=[str(item) for item in rubric],
        reference_answer=reference_answer,
        source_chat_ids=_normalize_source_chat_ids(
            raw.get("source_chat_ids"), ability=ability, conv_dir=conv_dir
        ),
        extras={key: value for key, value in raw.items() if key not in consumed},
    )


def load_beam_conversation(conv_dir: Path, tier: str) -> BeamConversation:
    """Load one BEAM conversation directory (chat + probing questions).

    Prefers ``chat_trunecated.json`` (sic — upstream misspelling) over
    ``chat.json`` when present, mirroring upstream's answer-generation batch
    worker which reads the truncated variant.
    """
    chat_file = "chat_trunecated.json"
    chat_path = conv_dir / chat_file
    if not chat_path.exists():
        chat_file = "chat.json"
        chat_path = conv_dir / chat_file
    if not chat_path.exists():
        raise FileNotFoundError(f"BEAM conversation has no chat file: {conv_dir}")

    chat_payload = json.loads(chat_path.read_text(encoding="utf-8"))
    if not isinstance(chat_payload, list) or not chat_payload:
        raise ValueError(f"BEAM chat file must be a non-empty list of batches: {chat_path}")
    batches = [_parse_batch(entry, conv_dir) for entry in chat_payload]

    probing_path = conv_dir / "probing_questions" / "probing_questions.json"
    if not probing_path.exists():
        raise FileNotFoundError(f"BEAM conversation has no probing questions: {probing_path}")
    probing_payload = json.loads(probing_path.read_text(encoding="utf-8"))
    if not isinstance(probing_payload, dict):
        raise ValueError(f"BEAM probing questions must be an object: {probing_path}")
    missing = [key for key in ABILITY_KEYS if key not in probing_payload]
    if missing:
        raise ValueError(f"BEAM probing questions missing abilities {missing}: {probing_path}")

    probes: list[BeamProbe] = []
    # Ability-major, upstream order preserved: (ability, index) is the
    # upstream identity of a probe, so ordering here is load-bearing.
    for ability in ABILITY_KEYS:
        records = probing_payload[ability]
        if not isinstance(records, list):
            raise ValueError(f"BEAM ability {ability!r} must map to a list: {probing_path}")
        for index, record in enumerate(records):
            probes.append(_parse_probe(record, ability=ability, index=index, conv_dir=conv_dir))

    return BeamConversation(
        conversation_id=conv_dir.name,
        tier=tier,
        source_chat_file=chat_file,
        batches=batches,
        probes=probes,
    )


def load_beam_tier(
    root: Path, tier: str, max_conversations: int | None = None
) -> list[BeamConversation]:
    """Load every conversation of one tier from a local BEAM ``chats/`` dir.

    ``root`` is the upstream ``chats/`` directory (containing 100K/500K/...).
    Conversation directories are numeric and loaded in numeric order.
    """
    if tier not in BEAM_TIERS:
        if tier.upper() == "10M":
            raise ValueError("BEAM 10M tier uses the plan-N combined layout; not supported in v1")
        raise ValueError(f"Unknown BEAM tier {tier!r}; supported tiers: {', '.join(BEAM_TIERS)}")

    tier_dir = root / tier
    if not tier_dir.is_dir():
        raise FileNotFoundError(f"BEAM tier directory not found: {tier_dir}")

    conv_dirs = sorted(
        (entry for entry in tier_dir.iterdir() if entry.is_dir() and entry.name.isdigit()),
        key=lambda entry: int(entry.name),
    )
    if not conv_dirs:
        raise ValueError(f"BEAM tier directory has no numeric conversation dirs: {tier_dir}")
    if max_conversations is not None:
        conv_dirs = conv_dirs[:max_conversations]

    return [load_beam_conversation(conv_dir, tier) for conv_dir in conv_dirs]
