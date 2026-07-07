"""Opt-in live LiteLLM embedding evaluation harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider


RELATED_DOCUMENT = "OAuth login refresh tokens keep an authenticated web session active."
DISTRACTOR_DOCUMENT = "A sourdough starter ferments flour and water before bread baking."
QUERY_TEXT = "authentication login token flow"


@dataclass(frozen=True)
class LiteLLMLiveCase:
    """A real LiteLLM embedding model to exercise end-to-end."""

    name: str
    model: str
    dimensions: int
    api_key_env: str | None = None
    api_base: str | None = None
    document_input_type: str | None = None
    query_input_type: str | None = None
    forward_dimensions: bool | None = None


@dataclass(frozen=True)
class LiteLLMLiveResult:
    """Measured result for a live LiteLLM embedding case."""

    name: str
    model: str
    dimensions: int
    api_key_env: str | None
    document_input_type: str | None
    query_input_type: str | None
    forward_dimensions: bool | None
    related_score: float
    distractor_score: float
    min_norm: float
    max_norm: float
    embed_documents_latency_ms: float
    embed_query_latency_ms: float
    total_latency_ms: float


type ProviderFactory = Callable[..., Any]


def _required_string(case_data: Mapping[str, Any], key: str) -> str:
    value = case_data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"LiteLLM live case must include non-empty string field {key!r}")
    return value


def _optional_string(case_data: Mapping[str, Any], key: str) -> str | None:
    value = case_data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"LiteLLM live case field {key!r} must be a non-empty string")
    return value


def _optional_bool(case_data: Mapping[str, Any], key: str) -> bool | None:
    value = case_data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"LiteLLM live case field {key!r} must be a boolean")
    return value


def load_custom_cases(raw: str | None) -> list[LiteLLMLiveCase]:
    """Load additional live model cases from JSON."""
    if not raw:
        return []

    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError("LiteLLM live cases JSON must be an array")

    cases: list[LiteLLMLiveCase] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each LiteLLM live case must be a JSON object")

        case_data: dict[str, Any] = value
        dimensions = case_data.get("dimensions")
        if type(dimensions) is not int or dimensions <= 0:
            raise ValueError("LiteLLM live case must include positive integer field 'dimensions'")

        cases.append(
            LiteLLMLiveCase(
                name=_required_string(case_data, "name"),
                model=_required_string(case_data, "model"),
                dimensions=dimensions,
                api_key_env=_optional_string(case_data, "api_key_env"),
                api_base=_optional_string(case_data, "api_base"),
                document_input_type=_optional_string(case_data, "document_input_type"),
                query_input_type=_optional_string(case_data, "query_input_type"),
                forward_dimensions=_optional_bool(case_data, "forward_dimensions"),
            )
        )
    return cases


def configured_cases(
    environ: Mapping[str, str] | None = None,
    *,
    custom_cases_raw: str | None = None,
) -> list[LiteLLMLiveCase]:
    """Return built-in and user-supplied live cases whose credentials are available."""
    env = os.environ if environ is None else environ
    cases: list[LiteLLMLiveCase] = []

    if env.get("OPENAI_API_KEY"):
        cases.append(
            LiteLLMLiveCase(
                name="openai-text-embedding-3-small",
                model="openai/text-embedding-3-small",
                dimensions=1536,
                api_key_env="OPENAI_API_KEY",
            )
        )

    if env.get("COHERE_API_KEY"):
        cases.append(
            LiteLLMLiveCase(
                name="cohere-embed-english-v3",
                model="cohere/embed-english-v3.0",
                dimensions=1024,
                api_key_env="COHERE_API_KEY",
            )
        )

    raw = (
        custom_cases_raw
        if custom_cases_raw is not None
        else env.get("BASIC_MEMORY_TEST_LITELLM_CASES")
    )
    cases.extend(load_custom_cases(raw))
    return cases


def cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity for live ranking sanity checks."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = vector_norm(a)
    norm_b = vector_norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_norm(vector: list[float]) -> float:
    """Return the Euclidean norm of a vector."""
    return math.sqrt(sum(value * value for value in vector))


def assert_valid_vector(vector: list[float], dimensions: int) -> float:
    """Assert provider output is a usable normalized vector and return its norm."""
    assert len(vector) == dimensions
    assert all(math.isfinite(value) for value in vector)
    norm = vector_norm(vector)
    assert math.isclose(norm, 1.0, abs_tol=1e-6)
    return norm


async def evaluate_case(
    case: LiteLLMLiveCase,
    *,
    environ: Mapping[str, str] | None = None,
    provider_factory: ProviderFactory = LiteLLMEmbeddingProvider,
) -> LiteLLMLiveResult:
    """Run one live LiteLLM case and return measured ranking/vector metrics."""
    env = os.environ if environ is None else environ
    api_key = env.get(case.api_key_env) if case.api_key_env else None
    provider = provider_factory(
        model_name=case.model,
        dimensions=case.dimensions,
        batch_size=2,
        api_key=api_key,
        api_base=case.api_base,
        timeout=60.0,
        document_input_type=case.document_input_type,
        query_input_type=case.query_input_type,
        forward_dimensions=case.forward_dimensions,
    )

    start = time.perf_counter()
    documents_start = time.perf_counter()
    vectors = await provider.embed_documents([RELATED_DOCUMENT, DISTRACTOR_DOCUMENT])
    documents_elapsed = time.perf_counter() - documents_start

    query_start = time.perf_counter()
    query_vector = await provider.embed_query(QUERY_TEXT)
    query_elapsed = time.perf_counter() - query_start
    total_elapsed = time.perf_counter() - start

    assert len(vectors) == 2
    norms = [assert_valid_vector(vector, case.dimensions) for vector in [*vectors, query_vector]]
    related_score = cosine(query_vector, vectors[0])
    distractor_score = cosine(query_vector, vectors[1])
    assert related_score > distractor_score, (
        f"{case.name} ranked the related document at {related_score:.4f}, "
        f"not above distractor {distractor_score:.4f}"
    )

    return LiteLLMLiveResult(
        name=case.name,
        model=case.model,
        dimensions=case.dimensions,
        api_key_env=case.api_key_env,
        document_input_type=case.document_input_type,
        query_input_type=case.query_input_type,
        forward_dimensions=case.forward_dimensions,
        related_score=related_score,
        distractor_score=distractor_score,
        min_norm=min(norms),
        max_norm=max(norms),
        embed_documents_latency_ms=documents_elapsed * 1000,
        embed_query_latency_ms=query_elapsed * 1000,
        total_latency_ms=total_elapsed * 1000,
    )


async def evaluate_cases(
    cases: Sequence[LiteLLMLiveCase],
    *,
    environ: Mapping[str, str] | None = None,
    provider_factory: ProviderFactory = LiteLLMEmbeddingProvider,
) -> tuple[list[LiteLLMLiveResult], list[tuple[LiteLLMLiveCase, str]]]:
    """Evaluate cases sequentially and collect all failures instead of failing fast."""
    env = os.environ if environ is None else environ
    results: list[LiteLLMLiveResult] = []
    failures: list[tuple[LiteLLMLiveCase, str]] = []

    for case in cases:
        if case.api_key_env and not env.get(case.api_key_env):
            failures.append((case, f"missing required env var {case.api_key_env}"))
            continue
        try:
            results.append(
                await evaluate_case(case, environ=env, provider_factory=provider_factory)
            )
        except Exception as exc:  # pragma: no cover - exercised by live provider failures
            failures.append((case, f"{type(exc).__name__}: {exc}"))

    return results, failures


def build_results_table(
    results: Sequence[LiteLLMLiveResult],
    failures: Sequence[tuple[LiteLLMLiveCase, str]],
) -> Table:
    """Build a rich report table for live LiteLLM results."""
    table = Table(title="LiteLLM Live Embedding Evaluation", show_lines=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Case", style="cyan", no_wrap=True)
    table.add_column("Model", style="magenta")
    table.add_column("Dims", justify="right")
    table.add_column("Roles", no_wrap=True)
    table.add_column("Forward dims", no_wrap=True)
    table.add_column("Related", justify="right")
    table.add_column("Distractor", justify="right")
    table.add_column("Norm", justify="right")
    table.add_column("Total ms", justify="right")

    for result in results:
        roles = _roles_label(result.document_input_type, result.query_input_type)
        table.add_row(
            "[green]PASS[/green]",
            result.name,
            result.model,
            str(result.dimensions),
            roles,
            _bool_label(result.forward_dimensions),
            f"{result.related_score:.4f}",
            f"{result.distractor_score:.4f}",
            f"{result.min_norm:.4f}-{result.max_norm:.4f}",
            f"{result.total_latency_ms:.1f}",
        )

    for case, reason in failures:
        roles = _roles_label(case.document_input_type, case.query_input_type)
        table.add_row(
            "[red]FAIL[/red]",
            case.name,
            case.model,
            str(case.dimensions),
            roles,
            _bool_label(case.forward_dimensions),
            "-",
            "-",
            "-",
            reason,
        )

    return table


def _roles_label(document_input_type: str | None, query_input_type: str | None) -> str:
    if document_input_type or query_input_type:
        return f"{document_input_type or '-'} / {query_input_type or '-'}"
    return "auto"


def _bool_label(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "auto"


def _load_cases_arg(args: argparse.Namespace) -> str | None:
    if args.cases_json and args.cases_file:
        raise ValueError("Use --cases-json or --cases-file, not both")
    if args.cases_json:
        return str(args.cases_json)
    if args.cases_file:
        return Path(args.cases_file).read_text(encoding="utf-8")
    return None


async def _async_main(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    console = Console()

    # Trigger: this command performs real network calls and can spend API quota.
    # Why: keeping the same explicit opt-in guard as pytest prevents accidental
    # live calls when someone discovers the harness through `just --list`.
    # Outcome: humans get a clear command to run before any provider is called.
    if environ.get("BASIC_MEMORY_RUN_LITELLM_INTEGRATION") != "1":
        console.print(
            "[red]Set BASIC_MEMORY_RUN_LITELLM_INTEGRATION=1 to run live LiteLLM "
            "provider checks.[/red]"
        )
        return 2

    custom_cases_raw = _load_cases_arg(args)
    cases = configured_cases(environ, custom_cases_raw=custom_cases_raw)
    if not cases:
        console.print(
            "[yellow]No LiteLLM live cases configured.[/yellow]\n"
            "Set OPENAI_API_KEY, COHERE_API_KEY, or provide custom cases with "
            "BASIC_MEMORY_TEST_LITELLM_CASES / --cases-file."
        )
        return 2

    results, failures = await evaluate_cases(cases, environ=environ)

    if args.json:
        payload = {
            "results": [asdict(result) for result in results],
            "failures": [{"case": asdict(case), "reason": reason} for case, reason in failures],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        console.print(build_results_table(results, failures))

    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for live LiteLLM evaluation."""
    parser = argparse.ArgumentParser(description="Run opt-in live LiteLLM embedding checks")
    parser.add_argument(
        "--cases-json",
        help="JSON array of custom LiteLLM cases; overrides BASIC_MEMORY_TEST_LITELLM_CASES",
    )
    parser.add_argument(
        "--cases-file",
        help="Path to a JSON file containing custom LiteLLM cases",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_async_main(args, os.environ))
    except ValueError as exc:
        Console(stderr=True).print(f"[red]{exc}[/red]")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
