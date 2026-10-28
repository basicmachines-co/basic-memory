"""Runtime isolation and readiness for the release comparison."""

from pathlib import Path

import pytest

from basic_memory_benchmarks.bm_runtime import isolated_bm_env, status_json_is_ready


@pytest.mark.parametrize("phase,ready", [("idle", True), ("never_indexed", False), ("pending", False)])
def test_current_status_readiness(phase: str, ready: bool) -> None:
    assert status_json_is_ready({"readiness": {"phase": phase}}) is ready


def test_unknown_readiness_is_not_assumed_ready() -> None:
    assert status_json_is_ready({"readiness": {"phase": "unknown"}}) is None


def test_runtime_removes_ambient_product_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "true")
    monkeypatch.setenv("BASIC_MEMORY_DATABASE_URL", "postgresql://ambient")
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")
    monkeypatch.setenv("BENCHMARK_TEST_TRANSPORT", "preserved")
    monkeypatch.chdir(tmp_path)
    env = isolated_bm_env(Path("runtime"))
    assert {key for key in env if key.startswith("BASIC_MEMORY_")} == {
        "BASIC_MEMORY_CONFIG_DIR",
        "BASIC_MEMORY_HOME",
        "BASIC_MEMORY_AUTO_UPDATE",
    }
    assert env["BASIC_MEMORY_HOME"] == str(tmp_path / "runtime/default-home")
    assert env["BASIC_MEMORY_AUTO_UPDATE"] == "false"
    assert env["BENCHMARK_TEST_TRANSPORT"] == "preserved"
