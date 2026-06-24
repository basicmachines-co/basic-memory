"""supermemory self-hosted server provider.

Targets supermemory-server (github.com/supermemoryai/supermemory, local
binary serving the Memory API on localhost:6767). The provider does not
manage the server process — the operator starts it and supplies:

- ``SUPERMEMORY_BASE_URL`` (default ``http://localhost:6767``)
- ``SUPERMEMORY_API_KEY`` (the ``sm_...`` key printed on the server's
  first boot; required — without it the provider is skipped)

Ingestion is async on the server (queued → extracting → chunking →
embedding → done | failed), so ingest() polls every document to a terminal
state before returning; searching before that point silently misses
content. Failed documents are reaped server-side after ~2 minutes, so a
poll 404 is treated as failed.

Benchmark scoping uses one container tag per run id; grouped runs get a
fresh tag per group automatically. cleanup() bulk-deletes the container.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import frontmatter
import httpx

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider

DEFAULT_BASE_URL = "http://localhost:6767"
_TERMINAL_STATUSES = {"done", "failed"}
_POLL_INTERVAL_SECONDS = 1.0


class SupermemoryLocalProvider(BenchmarkProvider):
    name = "supermemory-local"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.Client | None = None
        self._ingest_timeout_seconds = float(os.getenv("SUPERMEMORY_INGEST_TIMEOUT_S", "900"))
        self._ingested_failures: list[str] = []

    def _container_tag(self, run_config: RunConfig) -> str:
        return f"bm-bench-{run_config.run_id}"

    def _ensure_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client

        api_key = os.getenv("SUPERMEMORY_API_KEY")
        if not api_key:
            raise ProviderSkippedError(
                "SUPERMEMORY_API_KEY missing for supermemory-local provider "
                "(printed on supermemory-server first boot)"
            )
        base_url = os.getenv("SUPERMEMORY_BASE_URL", DEFAULT_BASE_URL)
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
            transport=self._transport,
        )

        # Fail fast (and skip cleanly) when the server isn't up at all.
        try:
            self._client.post("/v3/documents/list", json={"limit": 1, "page": 1})
        except httpx.ConnectError as exc:
            client = self._client
            self._client = None
            client.close()
            raise ProviderSkippedError(
                f"supermemory-server unreachable at {base_url}: {exc}"
            ) from exc
        return self._client

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        client = self._ensure_client()
        container_tag = self._container_tag(run_config)
        self._ingested_failures = []

        pending: dict[str, str] = {}  # server doc id -> our doc id
        for note_path in sorted(corpus_path.rglob("*.md")):
            rel_path = note_path.relative_to(corpus_path).as_posix()
            with note_path.open("r", encoding="utf-8") as handle:
                parsed = frontmatter.load(handle)
            doc_id = str(parsed.get("source_doc_id") or note_path.stem)
            response = client.post(
                "/v3/documents",
                json={
                    "content": parsed.content,
                    "customId": doc_id,
                    "containerTags": [container_tag],
                    "metadata": {
                        "source_doc_id": doc_id,
                        "source_path": rel_path,
                        "dataset_id": run_config.dataset_id,
                    },
                },
            )
            response.raise_for_status()
            server_id = str(response.json().get("id") or "")
            if not server_id:
                raise RuntimeError(f"supermemory add returned no id for {doc_id}")
            pending[server_id] = doc_id

        # Ingestion is async server-side; wait for every document to reach a
        # terminal state or searches will silently miss content.
        deadline = time.monotonic() + self._ingest_timeout_seconds
        while pending:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"supermemory ingestion timed out with {len(pending)} documents "
                    f"still processing (timeout {self._ingest_timeout_seconds}s)"
                )
            for server_id, doc_id in list(pending.items()):
                response = client.get(f"/v3/documents/{server_id}")
                if response.status_code == 404:
                    # Failed documents are reaped server-side after ~2 minutes.
                    self._ingested_failures.append(doc_id)
                    del pending[server_id]
                    continue
                response.raise_for_status()
                status = str(response.json().get("status") or "")
                if status in _TERMINAL_STATUSES:
                    if status == "failed":
                        self._ingested_failures.append(doc_id)
                    del pending[server_id]
            if pending:
                time.sleep(_POLL_INTERVAL_SECONDS)

        if self._ingested_failures:
            # Partial ingestion silently skews retrieval metrics; fail the
            # group/run loudly instead.
            raise RuntimeError(
                f"supermemory failed to ingest {len(self._ingested_failures)} documents: "
                f"{sorted(self._ingested_failures)[:10]}"
            )

    @staticmethod
    def _normalize_result(item: dict) -> SearchHit:
        metadata_raw = item.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        chunks = item.get("chunks") or []
        chunk_texts = [
            str(chunk.get("content") or "")
            for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("content")
        ]
        return SearchHit(
            id=str(item.get("documentId") or ""),
            source_doc_id=metadata.get("source_doc_id") or item.get("customId"),
            source_path=metadata.get("source_path"),
            text="\n".join(chunk_texts) if chunk_texts else item.get("title"),
            score=float(item.get("score") or 0.0),
            metadata=metadata,
        )

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        client = self._ensure_client()
        response = client.post(
            "/v3/search",
            json={
                "q": query,
                "limit": limit,
                "containerTags": [self._container_tag(run_config)],
            },
        )
        response.raise_for_status()
        rows = response.json().get("results") or []
        return [self._normalize_result(item) for item in rows if isinstance(item, dict)]

    def cleanup(self, run_config: RunConfig) -> None:
        if self._client is None:
            return
        try:
            self._client.request(
                "DELETE",
                "/v3/documents/bulk",
                json={"containerTags": [self._container_tag(run_config)]},
            )
        except Exception:
            # Cleanup should never break the main benchmark flow.
            pass
        finally:
            self._client.close()
            self._client = None

    def version_info(self) -> dict[str, str]:
        return {
            "supermemory_base_url": os.getenv("SUPERMEMORY_BASE_URL", DEFAULT_BASE_URL),
            "supermemory_server_version": os.getenv("SUPERMEMORY_SERVER_VERSION", "unknown"),
            "supermemory_transport": "http-v3",
        }
