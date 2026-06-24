"""Penfield Labs LoCoMo audit corrections.

The April 2026 audit of LoCoMo (github.com/dial481/locomo-audit) found 156
answer-key errors across the 1,540 usable questions: hallucinated facts,
temporal arithmetic mistakes, speaker-attribution errors, and wrong evidence
citations. Scoring against the corrected key (and citing the audit) is how a
published LoCoMo number survives scrutiny — the original key caps a perfect
system at roughly 93.6%.

Corrections are fetched from a pinned commit so runs are reproducible, merged
into one corrections.json keyed by the audit's question ids
(``locomo_<conv>_qa<index>``), and applied at conversion time with a
question-text cross-check so a dataset/audit drift fails loudly instead of
silently mis-correcting.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from basic_memory_benchmarks.models import DatasetProvenance
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso

# Pinned audit revision (main as of 2026-04-02). Bump deliberately.
LOCOMO_AUDIT_SHA = "9493fb4b4af4256ed17a18e8fd0b3cfdeec29539"
LOCOMO_AUDIT_REPO = "dial481/locomo-audit"
LOCOMO_AUDIT_LICENSE_NOTE = (
    "Audit corrections by Penfield Labs (github.com/dial481/locomo-audit); "
    "see repo LICENSE for terms."
)
_CONVERSATION_COUNT = 10

_REQUIRED_KEYS = {"question_id", "question", "error_type", "correct_answer"}


def _errors_url(conversation_index: int, sha: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{LOCOMO_AUDIT_REPO}/{sha}/"
        f"audit/errors_conv_{conversation_index}.json"
    )


def fetch_locomo_audit_corrections(
    output_path: Path, sha: str = LOCOMO_AUDIT_SHA
) -> DatasetProvenance:
    """Download all per-conversation error files and merge them into one list."""
    merged: list[dict] = []
    for conversation_index in range(_CONVERSATION_COUNT):
        response = httpx.get(_errors_url(conversation_index, sha), timeout=120)
        response.raise_for_status()
        records = response.json()
        if not isinstance(records, list):
            raise ValueError(
                f"Audit errors file for conversation {conversation_index} is not a list"
            )
        merged.extend(records)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    provenance = DatasetProvenance(
        dataset_id="locomo-audit",
        source_url=f"https://github.com/{LOCOMO_AUDIT_REPO}/tree/{sha}/audit",
        checksum_sha256=sha256_file(output_path),
        license_note=LOCOMO_AUDIT_LICENSE_NOTE,
        fetched_at_utc=utc_now_iso(),
    )
    output_path.with_suffix(".provenance.json").write_text(
        json.dumps(provenance.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return provenance


def load_locomo_corrections(path: Path) -> dict[str, dict]:
    """Load merged corrections keyed by audit question id."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Corrections file must contain a list: {path}")

    corrections: dict[str, dict] = {}
    for record in payload:
        if not isinstance(record, dict) or not _REQUIRED_KEYS.issubset(record):
            missing = _REQUIRED_KEYS - set(record) if isinstance(record, dict) else _REQUIRED_KEYS
            raise ValueError(f"Corrections record missing keys {sorted(missing)}: {path}")
        question_id = str(record["question_id"])
        if question_id in corrections:
            raise ValueError(f"Duplicate correction for {question_id}: {path}")
        corrections[question_id] = record
    return corrections
