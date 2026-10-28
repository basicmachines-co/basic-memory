"""Run alternating, isolated pairs of the existing MCP load benchmarks.

Example (from benchmarks/):
uv run python -m basic_memory_benchmarks.release_comparison \
  --baseline-checkout .scratch/product-v0.24.1 \
  --candidate-checkout .scratch/product-1.0-rc \
  --output-root .scratch/release-read-matrix --kind read --repeats 6
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from basic_memory_benchmarks.concurrent_write import resolve_clean_checkout_sha
from basic_memory_benchmarks.utils import sha256_file, utc_now_iso


def paired_order(repeats: int) -> list[tuple[int, str]]:
    """Alternate AB/BA; pair number is the statistical sampling unit."""
    return [
        (pair, label)
        for pair in range(1, repeats + 1)
        for label in (("baseline", "candidate") if pair % 2 else ("candidate", "baseline"))
    ]


def runtime_metadata(checkout: Path) -> dict[str, object]:
    source_sha = resolve_clean_checkout_sha(checkout)
    interpreter = checkout / ".venv/bin/python"
    probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            (
                "import basic_memory, importlib.metadata, json, platform; "
                "print(json.dumps({'module': basic_memory.__file__, "
                "'python': platform.python_version(), 'packages': "
                "{d.metadata['Name']: d.version for d in importlib.metadata.distributions()}}))"
            ),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    runtime = json.loads(probe.stdout)
    if not Path(runtime["module"]).resolve().is_relative_to(checkout / "src/basic_memory"):
        raise RuntimeError(f"runtime is not linked to pinned checkout: {checkout}")
    return {
        "sha": source_sha,
        "lock_sha256": sha256_file(checkout / "uv.lock"),
        "runtime": runtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-checkout", type=Path, required=True)
    parser.add_argument("--candidate-checkout", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--kind", choices=("read", "write"), default="read")
    parser.add_argument("--repeats", type=int, default=6)
    parser.add_argument("--concurrency", default="1,8,32,64")
    parser.add_argument("--reads", type=int, default=128)
    parser.add_argument("--notes-per-size", type=int, default=32)
    parser.add_argument("--sizes", default="1024,16384,65536")
    parser.add_argument("--semantic-search", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--write-notes", type=int, default=60)
    args = parser.parse_args()
    if min(args.repeats, args.reads, args.notes_per_size, args.write_notes) < 1:
        parser.error("repeat and workload counts must be positive")

    benchmark_root = Path(__file__).resolve().parents[2]
    harness_sha = resolve_clean_checkout_sha(benchmark_root)
    checkouts = {
        "baseline": args.baseline_checkout.resolve(),
        "candidate": args.candidate_checkout.resolve(),
    }
    products = {label: runtime_metadata(path) for label, path in checkouts.items()}
    baseline_runtime = products["baseline"]["runtime"]
    candidate_runtime = products["candidate"]["runtime"]
    if not isinstance(baseline_runtime, dict) or not isinstance(candidate_runtime, dict):
        raise TypeError("invalid runtime metadata")
    if baseline_runtime["python"] != candidate_runtime["python"]:
        raise ValueError("paired products must use the same Python version")

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    script = benchmark_root / "scripts" / f"{args.kind}_load_bench.py"
    matrix: dict[str, object] = {
        "kind": args.kind,
        "created_at_utc": utc_now_iso(),
        "benchmark_git_sha": harness_sha,
        "harness_python": sys.version,
        "harness_lock_sha256": sha256_file(benchmark_root / "uv.lock"),
        "products": products,
        "host": {"os": platform.platform(), "cpu_count": os.cpu_count()},
        "protocol": "sequential AB/BA pairs; warm authoritative SQLite; Redis disabled",
        "semantic_search": args.semantic_search if args.kind == "read" else "product-default",
        "runs": [],
    }
    runs: list[dict[str, object]] = []
    matrix["runs"] = runs
    matrix_path = output_root / "matrix.json"

    for pair, label in paired_order(args.repeats):
        run_dir = output_root / f"pair-{pair:02d}-{label}"
        run_dir.mkdir()
        surface = "legacy" if label == "baseline" else "bm"
        command = [
            sys.executable,
            str(script),
            "--bm-command",
            str(checkouts[label] / ".venv/bin/basic-memory"),
            "--surface",
            surface,
            "--label",
            f"{label}-pair-{pair:02d}",
            "--scratch",
            str(run_dir / "runtime"),
            "--output",
            str(run_dir / "results.jsonl"),
            "--concurrency",
            args.concurrency,
        ]
        if args.kind == "read":
            command += [
                "--sizes",
                args.sizes,
                "--notes-per-size",
                str(args.notes_per_size),
                "--reads",
                str(args.reads),
                "--semantic-search",
                args.semantic_search,
                "--seed-concurrency",
                "1",
            ]
        else:
            command += ["--notes", str(args.write_notes), "--warmup", "8"]
        record: dict[str, object] = {
            "pair": pair,
            "label": label,
            "surface": surface,
            "command": command,
            "started_at_utc": utc_now_iso(),
            "host_load_before": os.getloadavg(),
            "state": "running",
        }
        runs.append(record)
        matrix_path.write_text(json.dumps(matrix, indent=2) + "\n")
        print(f"{args.kind}: pair {pair}/{args.repeats} {label}", flush=True)
        with (run_dir / "process.log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        record.update(
            {
                "finished_at_utc": utc_now_iso(),
                "host_load_after": os.getloadavg(),
                "exit_code": completed.returncode,
                "state": "complete" if completed.returncode == 0 else "failed",
            }
        )
        matrix_path.write_text(json.dumps(matrix, indent=2) + "\n")
        if completed.returncode:
            raise RuntimeError(f"{label} pair {pair} failed; inspect {run_dir / 'process.log'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
