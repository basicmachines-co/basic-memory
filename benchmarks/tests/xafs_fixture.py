"""Hand-written miniature xAFS-layout fixture builders.

Mirrors the upstream supermemory/xAFS on-disk layout
(``<root>/dp_NNN/data/...`` + ``<root>/dp_NNN/question.json``) at toy scale:
two personas covering all three question families, one non-markdown file (the
``.eml`` mail) and one double-suffix table (``.csv.md``). Everything is built
programmatically into tmp_path — no upstream data is vendored and no network
is touched (the beam_fixture pattern).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Gold values shared by loader, converter, and driver tests.
DP1_INVOICE_AMOUNT = "$2,034"
DP1_REFERRER = "Dana Whitfield"
DP1_DUE_DATE = "2026-05-01"
DP1_CROSS_FORMAT_ANSWER = "June 2026"
DP2_GOAL_PACE = "8:45 per mile"
DP2_TRAINING_SUMMARY = "Portland half marathon; longest run 9 miles"

# Distinctive prompt phrasings: driver tests key a ScriptedToolAgent by
# substring match on these, so no prompt may contain another prompt's key.
DP1_Q1_PROMPT = "What was the amount of the Acme onboarding invoice?"
DP1_Q2_PROMPT = "Who referred the client that was billed for onboarding?"
DP1_Q3_PROMPT = "According to the invoice email, when is payment due?"
DP1_Q4_PROMPT = "In which month did monthly revenue first exceed the invoiced amount?"
DP2_Q1_PROMPT = "What goal pace is set for the half marathon?"
DP2_Q2_PROMPT = "Which race is being trained for, and how long was the longest run so far?"

DP1_FILES: Mapping[str, str] = {
    "data/client/kickoff-transcript.md": (
        "# Acme kickoff transcript\n"
        "\n"
        f"Kickoff call with Acme Corp. The onboarding invoice was {DP1_INVOICE_AMOUNT},\n"
        "covering setup and the first training block.\n"
    ),
    "data/memory/overview.md": (
        "# Client overview\n"
        "\n"
        f"Acme Corp came to us through a referral from {DP1_REFERRER} in March 2026.\n"
    ),
    # The non-markdown file: BM ingests .eml as a file resource, not a note.
    "data/mail/2026-04-01_invoice.eml": (
        "From: billing@acme.example\n"
        "To: ops@basicmachines.example\n"
        "Subject: Invoice INV-2034\n"
        "Date: Wed, 01 Apr 2026 09:00:00 +0000\n"
        "\n"
        f"Invoice INV-2034 for {DP1_INVOICE_AMOUNT} is attached.\n"
        f"Payment is due {DP1_DUE_DATE}.\n"
    ),
    # Double-suffix format file: ordinary markdown to the loader/converter.
    "data/notes/metrics.csv.md": (
        "# Monthly revenue\n"
        "\n"
        "| month | revenue |\n"
        "| --- | --- |\n"
        "| April 2026 | $1,500 |\n"
        "| May 2026 | $1,900 |\n"
        "| June 2026 | $2,600 |\n"
    ),
}

DP2_FILES: Mapping[str, str] = {
    "data/journal/2026-03-10.md": (
        "# 2026-03-10\n"
        "\n"
        "Started training for the Portland half marathon. Longest run so far: 9 miles.\n"
    ),
    "data/journal/2026-03-24.md": (
        f"# 2026-03-24\n\nRace day is 2026-06-14. Goal pace is {DP2_GOAL_PACE}.\n"
    ),
}


def question(
    qid: str,
    family: str,
    prompt: str,
    gold_file_ids: Sequence[str],
    gold_answer: str,
    **extras: Any,
) -> dict[str, Any]:
    """One upstream-shaped question record; ``extras`` become unknown keys."""
    record: dict[str, Any] = {
        "id": qid,
        "family": family,
        "prompt": prompt,
        "gold_file_ids": list(gold_file_ids),
        "gold_answer": gold_answer,
    }
    record.update(extras)
    return record


def dp1_questions() -> list[dict[str, Any]]:
    """Four questions across the three families; q01 carries an unknown key."""
    return [
        question(
            "q01",
            "single_hop",
            DP1_Q1_PROMPT,
            ["data/client/kickoff-transcript.md"],
            DP1_INVOICE_AMOUNT,
            difficulty="easy",  # unknown upstream key -> loader extras
        ),
        question(
            "q02",
            "multi_hop",
            DP1_Q2_PROMPT,
            ["data/memory/overview.md", "data/client/kickoff-transcript.md"],
            DP1_REFERRER,
        ),
        question(
            "q03",
            "format_spanning",
            DP1_Q3_PROMPT,
            ["data/mail/2026-04-01_invoice.eml"],
            DP1_DUE_DATE,
        ),
        question(
            "q04",
            "format_spanning",
            DP1_Q4_PROMPT,
            ["data/notes/metrics.csv.md", "data/mail/2026-04-01_invoice.eml"],
            DP1_CROSS_FORMAT_ANSWER,
        ),
    ]


def dp2_questions() -> list[dict[str, Any]]:
    return [
        question(
            "q01",
            "single_hop",
            DP2_Q1_PROMPT,
            ["data/journal/2026-03-24.md"],
            DP2_GOAL_PACE,
        ),
        question(
            "q02",
            "multi_hop",
            DP2_Q2_PROMPT,
            ["data/journal/2026-03-10.md", "data/journal/2026-03-24.md"],
            DP2_TRAINING_SUMMARY,
        ),
    ]


def write_persona(
    root: Path,
    persona_id: str,
    files: Mapping[str, str],
    questions: Sequence[Mapping[str, Any]],
) -> Path:
    """Write one persona dir in the upstream layout; returns the persona dir."""
    persona_dir = root / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        path = persona_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (persona_dir / "question.json").write_text(json.dumps(list(questions)), encoding="utf-8")
    return persona_dir


def write_xafs_root(tmp_path: Path) -> Path:
    """The two-persona miniature snapshot; returns the dataset root."""
    root = tmp_path / "xafs-upstream"
    write_persona(root, "dp_001", DP1_FILES, dp1_questions())
    write_persona(root, "dp_002", DP2_FILES, dp2_questions())
    return root
