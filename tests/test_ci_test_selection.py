"""Exercise complete CI partitions and the separate local incremental recipes."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_tests.py"
SPEC = importlib.util.spec_from_file_location("run_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
splitter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(splitter)


def test_partitions_are_balanced_disjoint_and_exhaustive() -> None:
    files = [Path(f"tests/test_{index}.py") for index in range(6)]
    durations = {f"{path}::test_example": float(index + 1) for index, path in enumerate(files)}
    shards = splitter.split_files(files, durations, 3)
    assert sorted(path for shard in shards for path in shard) == files
    totals = [sum(durations[f"{path}::test_example"] for path in shard) for shard in shards]
    assert totals == [7.0, 7.0, 7.0]
    assert splitter.split_files(list(reversed(files)), durations, 3) == shards


def test_new_files_are_included_and_obsolete_timings_are_ignored() -> None:
    files = [Path("tests/test_known.py"), Path("tests/test_new.py")]
    durations = {"tests/test_known.py::test_one": 5.0, "deleted.py::test_old": 1000.0}
    assert splitter.split_files(files, durations, 2) == [[files[0]], [files[1]]]


def test_cold_suite_balances_file_sizes(tmp_path: Path) -> None:
    files = [tmp_path / f"test_{index}.py" for index in range(4)]
    for index, path in enumerate(files):
        path.write_text("#" * (index + 1))
    shards = splitter.split_files(files, {}, 2)
    assert [sum(path.stat().st_size for path in shard) for shard in shards] == [5, 5]


def test_zero_duration_files_do_not_all_land_on_one_shard() -> None:
    files = [Path(f"test_{index}.py") for index in range(4)]
    durations = {f"{path}::test_one": 0.0 for path in files}
    assert [len(shard) for shard in splitter.split_files(files, durations, 2)] == [2, 2]
    with pytest.raises(ValueError, match="positive"):
        splitter.split_files(files, durations, 0)


def test_cli_rejects_missing_suite(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--groups", "2", "--group", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "no test files found" in result.stderr


def test_cli_lists_files_and_does_not_discover_tests_for_an_empty_shard(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("raise AssertionError('must not be imported')\n")
    assigned = tmp_path / "assigned.txt"
    command = [sys.executable, str(SCRIPT), str(tmp_path), "--splits", "2"]
    listed = subprocess.run(
        [*command, "--group", "1", "--list-files", "--file-list", str(assigned)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.strip() == test_file.as_posix()
    assert assigned.read_text() == listed.stdout
    empty = subprocess.run([*command, "--group", "2"], capture_output=True, text=True, check=False)
    assert empty.returncode == 0, empty.stderr
    assert "has no test files" in empty.stdout
    invalid = subprocess.run(
        [*command, "--group", "3"], capture_output=True, text=True, check=False
    )
    assert invalid.returncode == 2


@pytest.mark.parametrize("exit_code", [1, 137])
def test_runner_preserves_abrupt_pytest_exit(tmp_path: Path, exit_code: int) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_exit.py").write_text(
        f"import os\ndef test_exit():\n    os._exit({exit_code})\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "-q"],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr


def test_shards_run_all_tests_without_testmon_or_tracing(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    for index in range(4):
        (tmp_path / f"test_{index}.py").write_text(
            f"import sys\ndef test_value_{index}():\n    assert sys.gettrace() is None\n"
        )
    for _ in range(2):
        for group in (1, 2):
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(tmp_path),
                    "--splits",
                    "2",
                    "--group",
                    str(group),
                    "-q",
                ],
                cwd=tmp_path,
                env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "2 passed" in result.stdout
    assert not (tmp_path / ".testmondata").exists()


def test_ci_has_no_dependency_selection_or_testmon_caches() -> None:
    workflow_text = (ROOT / ".github/workflows/test.yml").read_text()
    assert "testmon" not in workflow_text
    workflow = yaml.safe_load(workflow_text)
    assert "pytest-flags" not in workflow["jobs"]["changes"]["outputs"]


@pytest.mark.parametrize("exit_code", [1, 2, 137])
def test_postgres_recipe_propagates_failed_or_killed_pytest(tmp_path: Path, exit_code: int) -> None:
    # Replace only the external process: exercise the actual just recipe without
    # starting a database or waiting ten minutes to reproduce a killed test run.
    uv = tmp_path / "uv"
    uv.write_text(f"#!/bin/sh\necho 'test process invoked'\nexit {exit_code}\n")
    uv.chmod(0o755)
    result = subprocess.run(
        ["just", "test-int-postgres"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "test process invoked" in result.stdout, result.stderr
    assert result.returncode != 0


def test_just_preserves_quoted_pytest_arguments(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\nprintf "<%s>\\n" "$@"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        ["just", "test-unit-sqlite", "-k", "first_case or second_case"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "BASIC_MEMORY_PYTEST_FLAGS": "--import-mode=importlib --testmon-noselect",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "<first_case or second_case>" in result.stdout
    assert "<--testmon-noselect>" in result.stdout


def test_refresh_records_the_environment_used_by_fast_test(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\nprintf "<%s>\\n" "$@"\n')
    uv.chmod(0o755)
    outputs = []
    for recipe in ["testmon-refresh", "fast-test"]:
        result = subprocess.run(
            ["just", recipe],
            cwd=ROOT,
            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert "<--testmon-noselect>" in outputs[0]
    assert "<--testmon-forceselect>" in outputs[1]
    assert next(line for line in outputs[0].splitlines() if "--testmon-env=" in line) == next(
        line for line in outputs[1].splitlines() if "--testmon-env=" in line
    )


def test_cli_smoke_ignores_incremental_selection_flags(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\nprintf "<%s>\\n" "$@"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        ["just", "test-cli-smoke", "--durations=15"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "BASIC_MEMORY_PYTEST_FLAGS": "--testmon --testmon-forceselect",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "<no:pytest-testmon>" in result.stdout
    assert "<--testmon>" not in result.stdout
    assert "<--testmon-forceselect>" not in result.stdout
    assert "<tests/cli/test_cli_tool_exit.py>" in result.stdout
    assert "<--durations=15>" in result.stdout
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
    job = workflow["jobs"]["test-cli-smoke"]
    assert job["if"] == "needs.changes.outputs.code == 'true'"
    assert any("just test-cli-smoke" in step.get("run", "") for step in job["steps"])
