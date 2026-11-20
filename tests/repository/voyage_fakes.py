"""A stand-in for the voyageai SDK that records requests and replays canned responses."""

from types import SimpleNamespace
from typing import Any


class FakeVoyageError(Exception):
    def __init__(self, message: str | None = None, json_body: Any = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.json_body = json_body


def _error_class(name: str) -> type[FakeVoyageError]:
    return type(name, (FakeVoyageError,), {})


ERRORS = SimpleNamespace(
    VoyageError=FakeVoyageError,
    **{
        name: _error_class(name)
        for name in (
            "APIError",
            "Timeout",
            "APIConnectionError",
            "RateLimitError",
            "ServerError",
            "ServiceUnavailableError",
            "TryAgain",
            "AuthenticationError",
            "InvalidRequestError",
        )
    },
)


class FakeAsyncClient:
    """Returns the next queued response (or raises it) for every endpoint call."""

    instances: list["FakeAsyncClient"] = []
    responses: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.tokenized: list[list[str]] = []
        FakeAsyncClient.instances.append(self)

    async def _respond(self, endpoint: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self.calls.append((endpoint, args, kwargs))
        response = FakeAsyncClient.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def tokenize(self, texts: list[str], model: str) -> list[SimpleNamespace]:
        """Count one token per character, so tests control batch weight with text length."""
        self.tokenized.append(texts)
        return [SimpleNamespace(ids=list(range(len(text)))) for text in texts]

    async def embed(self, *args: Any, **kwargs: Any) -> Any:
        return await self._respond("embed", args, kwargs)

    async def contextualized_embed(self, *args: Any, **kwargs: Any) -> Any:
        return await self._respond("contextualized_embed", args, kwargs)

    async def rerank(self, *args: Any, **kwargs: Any) -> Any:
        return await self._respond("rerank", args, kwargs)


def install_fake_voyageai(monkeypatch: Any, *responses: Any) -> type[FakeAsyncClient]:
    """Route both Voyage providers to the fake SDK, queueing the given responses."""
    FakeAsyncClient.instances = []
    FakeAsyncClient.responses = list(responses)
    sdk = SimpleNamespace(AsyncClient=FakeAsyncClient, error=ERRORS)
    for module in (
        "basic_memory.repository.voyage_provider",
        "basic_memory.repository.voyage_rerank_provider",
    ):
        monkeypatch.setattr(f"{module}.import_voyageai", lambda: sdk)
    return FakeAsyncClient


def embedding_response(*vectors: list[float]) -> SimpleNamespace:
    return SimpleNamespace(embeddings=list(vectors))


def contextualized_response(*chunks_per_document: list[list[float]]) -> SimpleNamespace:
    return SimpleNamespace(
        results=[SimpleNamespace(embeddings=chunks) for chunks in chunks_per_document]
    )


def rerank_response(*scores_by_index: tuple[int, float]) -> SimpleNamespace:
    return SimpleNamespace(
        results=[
            SimpleNamespace(index=index, relevance_score=score) for index, score in scores_by_index
        ]
    )
