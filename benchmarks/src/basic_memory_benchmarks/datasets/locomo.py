"""LoCoMo dataset utilities."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from basic_memory_benchmarks.models import DatasetProvenance
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso


LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
LOCOMO_LICENSE_NOTE = "Dataset is owned by source authors; redistribution may be restricted."


def fetch_locomo_dataset(output_path: Path, url: str = LOCOMO_URL) -> DatasetProvenance:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = httpx.get(url, timeout=120)
    response.raise_for_status()
    output_path.write_bytes(response.content)

    checksum = sha256_file(output_path)
    provenance = DatasetProvenance(
        dataset_id="locomo",
        source_url=url,
        checksum_sha256=checksum,
        license_note=LOCOMO_LICENSE_NOTE,
        fetched_at_utc=utc_now_iso(),
    )
    output_path.with_suffix(".provenance.json").write_text(
        json.dumps(provenance.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return provenance


def load_locomo_dataset(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload.get("conversations"), list):
            return payload["conversations"]
    raise ValueError(f"Unsupported LoCoMo payload shape in {path}")
