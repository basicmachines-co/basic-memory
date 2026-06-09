from pathlib import Path

import pytest

from scripts import testmon_cache


def _write_testmon_file(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_seed_testmon_data_reports_missing_shared_cache(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    repo_root.mkdir()

    result = testmon_cache.seed_testmon_data(repo_root=repo_root, cache_dir=cache_dir)

    assert result.status == "missing"
    assert result.copied == ()
    assert not (repo_root / ".testmondata").exists()


def test_seed_testmon_data_keeps_existing_local_data(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    local_datafile = _write_testmon_file(repo_root, ".testmondata", "local")
    _write_testmon_file(cache_dir, ".testmondata", "shared")

    result = testmon_cache.seed_testmon_data(repo_root=repo_root, cache_dir=cache_dir)

    assert result.status == "exists"
    assert result.copied == ()
    assert local_datafile.read_text(encoding="utf-8") == "local"


def test_seed_testmon_data_replaces_stale_sidecars(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    _write_testmon_file(repo_root, ".testmondata-shm", "stale sidecar")
    _write_testmon_file(cache_dir, ".testmondata", "shared main")
    _write_testmon_file(cache_dir, ".testmondata-wal", "shared wal")

    result = testmon_cache.seed_testmon_data(repo_root=repo_root, cache_dir=cache_dir)

    assert result.status == "seeded"
    assert {path.name for path in result.copied} == {".testmondata", ".testmondata-wal"}
    assert (repo_root / ".testmondata").read_text(encoding="utf-8") == "shared main"
    assert (repo_root / ".testmondata-wal").read_text(encoding="utf-8") == "shared wal"
    assert not (repo_root / ".testmondata-shm").exists()


def test_refresh_testmon_data_requires_local_data(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    repo_root.mkdir()

    with pytest.raises(FileNotFoundError):
        testmon_cache.refresh_testmon_data(repo_root=repo_root, cache_dir=cache_dir)


def test_refresh_testmon_data_replaces_shared_cache(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    _write_testmon_file(repo_root, ".testmondata", "local main")
    _write_testmon_file(repo_root, ".testmondata-shm", "local shm")
    _write_testmon_file(cache_dir, ".testmondata", "old main")
    _write_testmon_file(cache_dir, ".testmondata-wal", "old wal")

    result = testmon_cache.refresh_testmon_data(repo_root=repo_root, cache_dir=cache_dir)

    assert result.status == "refreshed"
    assert {path.name for path in result.copied} == {".testmondata", ".testmondata-shm"}
    assert (cache_dir / ".testmondata").read_text(encoding="utf-8") == "local main"
    assert (cache_dir / ".testmondata-shm").read_text(encoding="utf-8") == "local shm"
    assert not (cache_dir / ".testmondata-wal").exists()


def test_resolve_cache_dir_prefers_explicit_path_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    env_cache_dir = tmp_path / "env-cache"
    explicit_cache_dir = tmp_path / "explicit-cache"
    repo_root.mkdir()
    monkeypatch.setenv(testmon_cache.TESTMON_CACHE_ENV, str(env_cache_dir))

    assert testmon_cache.resolve_cache_dir(repo_root) == env_cache_dir.resolve()
    assert (
        testmon_cache.resolve_cache_dir(repo_root, explicit_cache_dir)
        == explicit_cache_dir.resolve()
    )


def test_status_command_prints_local_and_shared_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    repo_root.mkdir()
    _write_testmon_file(repo_root, ".testmondata", "local main")

    exit_code = testmon_cache.main(
        ["--repo-root", str(repo_root), "--cache-dir", str(cache_dir), "status"]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"Repo root:      {repo_root.resolve()}" in output
    assert "Worktree ready: True" in output
    assert "Cache ready:    False" in output
