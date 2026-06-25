"""ConvoMem (Salesforce) dataset utilities.

ConvoMem ships ~75K QA pairs as pre-mixed test cases on HuggingFace
(Salesforce/ConvoMem, Apache-2.0): each case is a self-contained haystack of
conversations (evidence + filler) plus its questions, organized as
``core_benchmark/pre_mixed_testcases/<category>/<N>_evidence/batched_*.json``.
Cases map 1:1 onto the harness's grouped runner mode.

The full dataset is multi-GB (a single context-size-300 batch is ~850MB), so
fetching is selective: batch files within a directory are ordered by case
context size, and the last few KB of each file contain its final case's
``contextSize``. A cheap HTTP Range tail-probe indexes every file without
downloading it; only files matching the requested context sizes are fetched.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from basic_memory_benchmarks.models import DatasetProvenance
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso

CONVOMEM_REPO = "Salesforce/ConvoMem"
CONVOMEM_LICENSE_NOTE = "ConvoMem by Salesforce AI Research (Apache-2.0); see dataset card."
_BASE_PATH = "core_benchmark/pre_mixed_testcases"
_RESOLVE = f"https://huggingface.co/datasets/{CONVOMEM_REPO}/resolve/main"
_TREE = f"https://huggingface.co/api/datasets/{CONVOMEM_REPO}/tree/main"

# Benchmark categories and their lowest evidence level (per the dataset README;
# 'changing' requires >= 2 evidence items).
CATEGORY_EVIDENCE_LEVELS: dict[str, int] = {
    "user_evidence": 1,
    "assistant_facts_evidence": 1,
    "changing_evidence": 2,
    "abstention_evidence": 1,
    "preference_evidence": 1,
    "implicit_connection_evidence": 1,
}

DEFAULT_CONTEXT_SIZES = (10, 30)
_TAIL_PROBE_BYTES = 4096
_CONTEXT_SIZE_PATTERN = re.compile(r'"contextSize":\s*(\d+)')


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET with retries: rapid probe sequences trip transient CDN resets."""
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = client.get(url, follow_redirects=True, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after retries: {last_error}")


def _tail_context_size(client: httpx.Client, repo_path: str) -> int | None:
    """Read the final case's contextSize via an HTTP Range request."""
    response = _get_with_retry(
        client,
        f"{_RESOLVE}/{repo_path}",
        headers={"Range": f"bytes=-{_TAIL_PROBE_BYTES}"},
    )
    matches = _CONTEXT_SIZE_PATTERN.findall(response.text)
    return int(matches[-1]) if matches else None


def _list_batch_files(client: httpx.Client, category: str, level: int) -> list[str]:
    response = _get_with_retry(client, f"{_TREE}/{_BASE_PATH}/{category}/{level}_evidence")
    return sorted(
        item["path"]
        for item in response.json()
        if isinstance(item, dict) and item.get("path", "").endswith(".json")
    )


def fetch_convomem_batches(
    output_dir: Path,
    context_sizes: tuple[int, ...] = DEFAULT_CONTEXT_SIZES,
    categories: dict[str, int] | None = None,
) -> DatasetProvenance:
    """Index every batch file by tail-probe and download only matching ones.

    Downloaded files are stored flat as ``<category>__<level>__<name>.json``;
    an ``index.json`` records the full probe results (including files NOT
    downloaded) so the selection itself is auditable.
    """
    categories = categories or CATEGORY_EVIDENCE_LEVELS
    output_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(context_sizes)

    index: list[dict] = []
    downloaded: list[Path] = []
    with httpx.Client(timeout=120) as client:
        for category, level in categories.items():
            for repo_path in _list_batch_files(client, category, level):
                # Throttle probes; HF's CDN resets connections on rapid bursts.
                time.sleep(0.2)
                tail_size = _tail_context_size(client, repo_path)
                record = {
                    "repo_path": repo_path,
                    "category": category,
                    "evidence_level": level,
                    "tail_context_size": tail_size,
                    "downloaded": tail_size in wanted,
                }
                index.append(record)
                if tail_size not in wanted:
                    continue
                local_name = f"{category}__{level}__{Path(repo_path).name}"
                local_path = output_dir / local_name
                with client.stream(
                    "GET", f"{_RESOLVE}/{repo_path}", follow_redirects=True
                ) as response:
                    response.raise_for_status()
                    with local_path.open("wb") as file:
                        for chunk in response.iter_bytes():
                            file.write(chunk)
                record["local_file"] = local_name
                record["sha256"] = sha256_file(local_path)
                downloaded.append(local_path)

    index_path = output_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {"context_sizes": sorted(wanted), "files": index},
            indent=2,
        ),
        encoding="utf-8",
    )

    provenance = DatasetProvenance(
        dataset_id="convomem",
        source_url=f"https://huggingface.co/datasets/{CONVOMEM_REPO}",
        checksum_sha256=sha256_file(index_path),
        license_note=CONVOMEM_LICENSE_NOTE,
        fetched_at_utc=utc_now_iso(),
    )
    index_path.with_suffix(".provenance.json").write_text(
        json.dumps(provenance.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    if not downloaded:
        raise RuntimeError(
            f"No ConvoMem batch files matched context sizes {sorted(wanted)}; "
            "see index.json for the probe results"
        )
    return provenance


def load_convomem_batches(batches_dir: Path) -> list[tuple[str, str, list[dict]]]:
    """Load downloaded batches as (category, local_file_name, cases) tuples."""
    results: list[tuple[str, str, list[dict]]] = []
    for path in sorted(batches_dir.glob("*__*__*.json")):
        category = path.name.split("__", 1)[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"ConvoMem batch must be a list of cases: {path}")
        results.append((category, path.name, payload))
    if not results:
        raise FileNotFoundError(f"No ConvoMem batch files found in {batches_dir}")
    return results
