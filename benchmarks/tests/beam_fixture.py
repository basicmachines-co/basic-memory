"""Hand-written miniature BEAM-format fixture builders.

Mirrors the upstream mohammadtavakoli78/BEAM on-disk layout
(``chats/<tier>/<N>/chat.json`` + ``probing_questions/probing_questions.json``)
at toy scale: two conversations, all ten ability keys present, with real
probes for information_extraction, knowledge_update, event_ordering, and
abstention. Everything is built programmatically into tmp_path — no upstream
data is vendored and no network is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from basic_memory_benchmarks.datasets.beam import ABILITY_KEYS, REFERENCE_ANSWER_FIELDS

# Phrasing chosen to trigger the binary QA judge's unanswerable branch, like
# real upstream ideal_response values ("...there is no information related to...").
ABSTENTION_IDEAL_RESPONSE = (
    "Unfortunately, there is no information related to your car in our previous conversations."
)


def message(
    role: str,
    message_id: int,
    content: str,
    *,
    question_type: str | None = None,
    time_anchor: str | None = None,
    index: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"role": role, "id": message_id, "content": content}
    if question_type is not None:
        raw["question_type"] = question_type
    if time_anchor is not None:
        raw["time_anchor"] = time_anchor
    if index is not None:
        raw["index"] = index
    return raw


def probe(
    ability: str,
    question: str,
    *,
    rubric: list[str],
    reference_answer: str,
    source_chat_ids: object = None,
    difficulty: str = "easy",
    **extras: Any,
) -> dict[str, Any]:
    """Build one upstream-shaped probe record.

    The reference answer lands in the per-ability field name
    (answer/ideal_response/expected_compliance/...), exactly as upstream ships
    it. ``source_chat_ids=None`` omits the field (the abstention shape).
    """
    record: dict[str, Any] = {
        "question": question,
        "difficulty": difficulty,
        "rubric": rubric,
        REFERENCE_ANSWER_FIELDS[ability]: reference_answer,
    }
    if source_chat_ids is not None:
        record["source_chat_ids"] = source_chat_ids
    record.update(extras)
    return record


def minimal_probes(source_chat_ids: list[int] | None = None) -> dict[str, list[dict[str, Any]]]:
    """One minimal probe per ability (the loader requires all ten keys)."""
    ids = [0] if source_chat_ids is None else source_chat_ids
    probes: dict[str, list[dict[str, Any]]] = {}
    for ability in ABILITY_KEYS:
        if ability == "abstention":
            probes[ability] = [
                probe(
                    ability,
                    "Minimal abstention probe?",
                    rubric=["abstention nugget"],
                    reference_answer=ABSTENTION_IDEAL_RESPONSE,
                )
            ]
        else:
            probes[ability] = [
                probe(
                    ability,
                    f"Minimal {ability} probe?",
                    rubric=[f"{ability} nugget"],
                    reference_answer=f"{ability} reference",
                    source_chat_ids=list(ids),
                )
            ]
    return probes


def conversation_one_chat() -> list[dict[str, Any]]:
    """Two batches: batch 1 anchored at batch level, batch 2 at message level.

    Message ids are global and interleaved (0..5). Message 2 carries the
    trailing ``->-> 1,2`` probe-index marker the converter must strip;
    message 3 carries the non-numeric ``->-> 2,N/A`` variant observed in
    upstream 100K/1/chat.json.
    """
    return [
        {
            "batch_number": 1,
            "time_anchor": "March-15-2024",
            "turns": [
                [
                    message("user", 0, "I adopted a puppy named Biscuit."),
                    message("assistant", 1, "Congratulations on adopting Biscuit!"),
                ],
                [
                    message(
                        "user",
                        2,
                        "My dentist appointment is on March 29. ->-> 1,2",
                        question_type="main_question",
                        index="1,2",
                    ),
                    message(
                        "assistant",
                        3,
                        "Noted: the dentist appointment is March 29. ->-> 2,N/A",
                    ),
                ],
            ],
        },
        {
            "batch_number": 2,
            "time_anchor": None,
            "turns": [
                [
                    message(
                        "user",
                        4,
                        "Update: my salary is now $75,000.",
                        time_anchor="April-02-2024",
                    ),
                    message("assistant", 5, "Got it, salary updated."),
                ]
            ],
        },
    ]


def conversation_one_probes() -> dict[str, list[dict[str, Any]]]:
    """All ten abilities; four carry real content used by converter tests."""
    probes = minimal_probes()
    probes["information_extraction"] = [
        probe(
            "information_extraction",
            "When is the dentist appointment?",
            rubric=["LLM response should state: March 29"],
            reference_answer="March 29",
            source_chat_ids=[2],
        )
    ]
    probes["knowledge_update"] = [
        probe(
            "knowledge_update",
            "What is my current salary?",
            rubric=["States the salary is $75,000"],
            reference_answer="$75,000",
            # The verified upstream dict shape — exercises normalization.
            source_chat_ids={"original_info": [1], "updated_info": [4]},
            difficulty="medium",
        )
    ]
    probes["event_ordering"] = [
        probe(
            "event_ordering",
            "List the events we discussed in the order they happened.",
            rubric=["Adopted Biscuit", "Dentist appointment", "Salary update"],
            reference_answer="Adopted Biscuit -> Dentist appointment -> Salary update",
            source_chat_ids=[0, 2, 4],
            difficulty="hard",
            ordering_type="full",
        )
    ]
    probes["abstention"] = [
        probe(
            "abstention",
            "What car do I drive?",
            rubric=["Acknowledges there is no information about a car"],
            reference_answer=ABSTENTION_IDEAL_RESPONSE,
            abstention_type="never_mentioned",
        )
    ]
    return probes


def conversation_two_full_chat() -> list[dict[str, Any]]:
    return [
        {
            "batch_number": 1,
            "time_anchor": None,
            "turns": [
                [
                    message("user", 0, "FULL VERSION: the meeting is on Friday."),
                    message("assistant", 1, "Understood."),
                ]
            ],
        }
    ]


def conversation_two_truncated_chat() -> list[dict[str, Any]]:
    return [
        {
            "batch_number": 1,
            "time_anchor": None,
            "turns": [
                [
                    message("user", 0, "TRUNCATED VERSION: the meeting is on Friday."),
                    message("assistant", 1, "Understood."),
                ]
            ],
        }
    ]


def write_conversation(
    conv_dir: Path,
    chat: list[dict[str, Any]],
    probes: dict[str, list[dict[str, Any]]],
    *,
    truncated_chat: list[dict[str, Any]] | None = None,
) -> Path:
    """Write one conversation dir in the upstream layout; returns the dir."""
    conv_dir.mkdir(parents=True)
    (conv_dir / "chat.json").write_text(json.dumps(chat), encoding="utf-8")
    if truncated_chat is not None:
        (conv_dir / "chat_trunecated.json").write_text(  # sic — upstream misspelling
            json.dumps(truncated_chat), encoding="utf-8"
        )
    probing_dir = conv_dir / "probing_questions"
    probing_dir.mkdir()
    (probing_dir / "probing_questions.json").write_text(json.dumps(probes), encoding="utf-8")
    return conv_dir


def write_beam_tier(chats_root: Path, tier: str = "100K") -> Path:
    """Write the two-conversation miniature tier; returns the chats root.

    Conversation 1 has the anchored two-batch chat with real probes;
    conversation 2 ships both chat.json and chat_trunecated.json so tests can
    assert the loader prefers the truncated variant.
    """
    write_conversation(chats_root / tier / "1", conversation_one_chat(), conversation_one_probes())
    write_conversation(
        chats_root / tier / "2",
        conversation_two_full_chat(),
        minimal_probes(),
        truncated_chat=conversation_two_truncated_chat(),
    )
    return chats_root
