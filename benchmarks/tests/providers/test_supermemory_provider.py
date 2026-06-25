"""Tests for the supermemory-local provider against a mocked v3 API."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.models import RunConfig
from basic_memory_benchmarks.providers.supermemory_local import SupermemoryLocalProvider


def _config(run_id: str = "smrun") -> RunConfig:
    return RunConfig(
        run_id=run_id, dataset_id="t", dataset_path="t", corpus_dir="t", queries_path="t"
    )


def _write_doc(corpus: Path, doc_id: str, body: str) -> None:
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / f"{doc_id}.md").write_text(
        f"---\ntitle: {doc_id}\nsource_doc_id: {doc_id}\n---\n\n{body}\n",
        encoding="utf-8",
    )


class FakeServer:
    """Minimal in-memory v3 API: add, status poll, search, bulk delete."""

    def __init__(self, fail_doc_ids: set[str] | None = None, polls_to_done: int = 1):
        self.docs: dict[str, dict] = {}
        self.deleted_containers: list[list[str]] = []
        self.fail_doc_ids = fail_doc_ids or set()
        self.polls_to_done = polls_to_done
        self.requests: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.headers.get("Authorization") != "Bearer sm_test":
            return httpx.Response(401, json={"error": "unauthorized"})

        if request.method == "POST" and request.url.path == "/v3/documents/list":
            return httpx.Response(200, json={"memories": [], "pagination": {}})

        if request.method == "POST" and request.url.path == "/v3/documents":
            body = json.loads(request.content)
            server_id = f"doc_{body['customId']}"
            self.docs[server_id] = {"body": body, "polls": 0}
            return httpx.Response(200, json={"id": server_id, "status": "queued"})

        if request.method == "GET" and request.url.path.startswith("/v3/documents/"):
            server_id = request.url.path.rsplit("/", 1)[-1]
            doc = self.docs.get(server_id)
            if doc is None:
                return httpx.Response(404, json={"error": "not found"})
            doc["polls"] += 1
            custom_id = doc["body"]["customId"]
            if doc["polls"] < self.polls_to_done:
                return httpx.Response(200, json={"id": server_id, "status": "embedding"})
            status = "failed" if custom_id in self.fail_doc_ids else "done"
            return httpx.Response(200, json={"id": server_id, "status": status})

        if request.method == "POST" and request.url.path == "/v3/search":
            body = json.loads(request.content)
            results = []
            for server_id, doc in self.docs.items():
                content = doc["body"]["content"]
                if any(term.lower() in content.lower() for term in body["q"].split()):
                    results.append(
                        {
                            "documentId": server_id,
                            "title": doc["body"]["customId"],
                            "score": 0.9,
                            "chunks": [{"content": content, "score": 0.95, "isRelevant": True}],
                            "metadata": doc["body"]["metadata"],
                        }
                    )
            return httpx.Response(
                200, json={"results": results[: body["limit"]], "total": len(results)}
            )

        if request.method == "DELETE" and request.url.path == "/v3/documents/bulk":
            body = json.loads(request.content)
            self.deleted_containers.append(body.get("containerTags") or [])
            return httpx.Response(200, json={"deleted": len(self.docs)})

        return httpx.Response(500, json={"error": f"unhandled {request.method} {request.url.path}"})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUPERMEMORY_API_KEY", "sm_test")
    monkeypatch.setenv("SUPERMEMORY_INGEST_TIMEOUT_S", "5")


def _provider(server: FakeServer) -> SupermemoryLocalProvider:
    return SupermemoryLocalProvider(transport=httpx.MockTransport(server.handler))


class TestSupermemoryProvider:
    def test_ingest_polls_to_done_then_search(self, env, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        server = FakeServer(polls_to_done=3)
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-austin", "Joanna moved to Austin last spring.")
        _write_doc(corpus, "doc-marathon", "Anthony runs marathons.")
        provider = _provider(server)

        provider.ingest(corpus, _config())
        hits = provider.search("Austin", 5, _config())

        assert [h.source_doc_id for h in hits] == ["doc-austin"]
        assert hits[0].score == 0.9
        assert "Joanna" in (hits[0].text or "")
        # Each doc was polled to terminal state.
        assert all(doc["polls"] >= 3 for doc in server.docs.values())

    def test_container_tag_scopes_run(self, env, tmp_path):
        server = FakeServer()
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "content here")
        provider = _provider(server)

        provider.ingest(corpus, _config("groupx"))

        added = server.docs["doc_doc-a"]["body"]
        assert added["containerTags"] == ["bm-bench-groupx"]

    def test_failed_document_raises(self, env, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        server = FakeServer(fail_doc_ids={"doc-bad"})
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-good", "fine content")
        _write_doc(corpus, "doc-bad", "doomed content")
        provider = _provider(server)

        with pytest.raises(RuntimeError, match="failed to ingest 1 documents"):
            provider.ingest(corpus, _config())

    def test_poll_404_treated_as_failure(self, env, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        server = FakeServer()
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "content")
        provider = _provider(server)

        original_handler = server.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.startswith("/v3/documents/"):
                return httpx.Response(404, json={"error": "reaped"})
            return original_handler(request)

        provider = SupermemoryLocalProvider(transport=httpx.MockTransport(handler))
        with pytest.raises(RuntimeError, match="failed to ingest"):
            provider.ingest(corpus, _config())

    def test_cleanup_bulk_deletes_container(self, env, tmp_path):
        server = FakeServer()
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "content")
        provider = _provider(server)

        provider.ingest(corpus, _config("wipeme"))
        provider.cleanup(_config("wipeme"))

        assert server.deleted_containers == [["bm-bench-wipeme"]]

    def test_skipped_without_api_key(self, monkeypatch):
        monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
        provider = SupermemoryLocalProvider()
        with pytest.raises(ProviderSkippedError, match="SUPERMEMORY_API_KEY"):
            provider.search("anything", 5, _config())

    def test_skipped_when_server_unreachable(self, env):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = SupermemoryLocalProvider(transport=httpx.MockTransport(refuse))
        with pytest.raises(ProviderSkippedError, match="unreachable"):
            provider.search("anything", 5, _config())

    def test_ingest_timeout_raises(self, env, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        # Never reaches done within the 5s budget because polls_to_done is huge.
        server = FakeServer(polls_to_done=10_000_000)
        monkeypatch.setenv("SUPERMEMORY_INGEST_TIMEOUT_S", "0.1")
        corpus = tmp_path / "docs"
        _write_doc(corpus, "doc-a", "content")
        provider = _provider(server)

        with pytest.raises(TimeoutError, match="timed out"):
            provider.ingest(corpus, _config())

    def test_factory_registration(self, env):
        from basic_memory_benchmarks.providers import create_provider

        assert isinstance(create_provider("supermemory-local"), SupermemoryLocalProvider)
