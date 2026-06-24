"""LongMemEval dataset utilities.

LongMemEval (Wu et al., ICLR 2025) evaluates long-term conversational memory
across six question types. The -S variant gives each of the 500 questions its
own ~50-session haystack, so the benchmark runs as grouped per-question
corpora rather than one shared corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from basic_memory_benchmarks.models import DatasetProvenance
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso

LONGMEMEVAL_S_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s"
)
LONGMEMEVAL_LICENSE_NOTE = "Dataset is owned by source authors; redistribution may be restricted."

_REQUIRED_KEYS = {
    "question_id",
    "question_type",
    "question",
    "answer",
    "question_date",
    "haystack_session_ids",
    "haystack_dates",
    "haystack_sessions",
    "answer_session_ids",
}


def fetch_longmemeval_dataset(output_path: Path, url: str = LONGMEMEVAL_S_URL) -> DatasetProvenance:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # The -S file is ~278MB; stream to disk instead of buffering in memory.
    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as response:
        response.raise_for_status()
        with output_path.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)

    checksum = sha256_file(output_path)
    provenance = DatasetProvenance(
        dataset_id="longmemeval_s",
        source_url=url,
        checksum_sha256=checksum,
        license_note=LONGMEMEVAL_LICENSE_NOTE,
        fetched_at_utc=utc_now_iso(),
    )
    output_path.with_suffix(".provenance.json").write_text(
        json.dumps(provenance.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return provenance


def load_longmemeval_dataset(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"LongMemEval payload must be a list of questions: {path}")
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict) or not _REQUIRED_KEYS.issubset(entry):
            missing = _REQUIRED_KEYS - set(entry) if isinstance(entry, dict) else _REQUIRED_KEYS
            raise ValueError(f"LongMemEval entry {index} missing keys {sorted(missing)}: {path}")
    return payload
