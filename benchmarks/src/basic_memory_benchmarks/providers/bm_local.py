"""Basic Memory local provider via warm MCP stdio contract."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from mcp.types import CallToolResult

from basic_memory_benchmarks.bm_runtime import (
    WarmMcpClient,
    resolve_bm_command_prefix,
    status_json_is_ready,
)
from basic_memory_benchmarks.models import RunConfig, SearchHit
from basic_memory_benchmarks.providers.base import BenchmarkProvider
from basic_memory_benchmarks.utils import run_command


class BasicMemoryLocalProvider(BenchmarkProvider):
    name = "bm-local"
    # One instance serves every group in a grouped run: the warm MCP session
    # and isolated config dir are shared, projects are per-group.
    supports_group_reuse = True

    def __init__(self) -> None:
        # run_id -> resolved project name (grouped runs ingest many projects).
        self._resolved_project_names: dict[str, str] = {}
        self._status_json_supported: bool | None = None
        self._mcp: WarmMcpClient | None = None
        self._bm_command_prefix: list[str] = ["bm"]
        self._bm_env: dict[str, str] | None = None
        self._config_dir: Path | None = None

    def _isolated_bm_env(self) -> dict[str, str]:
        # The benchmark must not depend on (or mutate) the operator's personal
        # Basic Memory config — e.g. a cloud-mode setup would route search_notes
        # through cloud.basicmemory.com. BASIC_MEMORY_CONFIG_DIR scopes config,
        # database, and project registry to a benchmark-owned directory.
        #
        # The directory is FRESH per provider instance: a persistent shared
        # home rots across basic-memory versions (alembic migrations from a
        # newer dev build brick older binaries) and leaks projects between
        # runs.
        #
        # BASIC_MEMORY_HOME must point inside the sandbox too. Unset, Basic
        # Memory registers its default project at ~/basic-memory, so every
        # provider instance indexed the operator's personal notes (485 of them
        # on 2026-09-03, embedded per run) into the benchmark database. Same
        # rule as bm_runtime.isolated_bm_env.
        if self._config_dir is None:
            root = Path("benchmarks/.bm-homes")
            root.mkdir(parents=True, exist_ok=True)
            self._config_dir = Path(tempfile.mkdtemp(prefix="bm-home-", dir=root))
        default_home = self._config_dir / "default-home"
        default_home.mkdir(exist_ok=True)
        env = dict(os.environ)
        env.pop("BASIC_MEMORY_CLOUD_MODE", None)
        env["BASIC_MEMORY_CONFIG_DIR"] = str(self._config_dir)
        env["BASIC_MEMORY_HOME"] = str(default_home)
        return env

    def _project_name(self, run_config: RunConfig) -> str:
        resolved = self._resolved_project_names.get(run_config.run_id)
        if resolved is not None:
            return resolved
        return f"bm-bench-{run_config.run_id}"

    @staticmethod
    def _resolve_bm_command_prefix(run_config: RunConfig) -> list[str]:
        return resolve_bm_command_prefix(run_config.bm_local_path)

    def _run_bm(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        return run_command(self._bm_command_prefix + args, check=check, env=self._bm_env)

    @staticmethod
    def _extract_existing_project_name(message: str) -> str | None:
        match = re.search(r"existing project\s+'([^']+)'", message, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _status_json_is_ready(payload: dict[str, Any]) -> bool | None:
        return status_json_is_ready(payload)

    @staticmethod
    def _error_text_from_result(result: CallToolResult) -> str:
        for item in result.content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return "Unknown MCP tool error"

    @classmethod
    def _payload_from_call_tool_result(cls, result: CallToolResult) -> dict[str, Any]:
        if result.isError:
            raise RuntimeError(cls._error_text_from_result(result))

        structured = result.structuredContent
        if isinstance(structured, dict):
            wrapped = structured.get("result")
            if isinstance(wrapped, dict):
                return cast(dict[str, Any], wrapped)
            return cast(dict[str, Any], structured)

        for item in result.content:
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            maybe_json = text.strip()
            if not maybe_json:
                continue
            try:
                parsed = json.loads(maybe_json)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)

        return {}

    def _supports_status_json(self) -> bool:
        if self._status_json_supported is not None:
            return self._status_json_supported

        probe = self._run_bm(["status", "--json", "--local"], check=False)
        merged = ((probe.stdout or "") + "\n" + (probe.stderr or "")).lower()
        self._status_json_supported = "no such option: --json" not in merged
        return self._status_json_supported

    def _wait_for_index_ready(self, project_name: str) -> None:
        if not self._supports_status_json():
            return

        deadline = time.monotonic() + 120.0
        delay = 0.25
        while True:
            completed = self._run_bm(
                [
                    "status",
                    "--project",
                    project_name,
                    "--json",
                    "--local",
                ]
            )
            payload = json.loads(completed.stdout.strip() or "{}")
            if isinstance(payload, dict):
                readiness = self._status_json_is_ready(cast(dict[str, Any], payload))
                if readiness is None:
                    return
                if readiness:
                    return

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for bm status --json readiness for project '{project_name}'"
                )
            # Small corpora index in well under a second; start polling fast
            # and back off instead of paying a fixed 2s floor per group.
            time.sleep(delay)
            delay = min(delay * 2, 2.0)

    def ingest(self, corpus_path: Path, run_config: RunConfig) -> None:
        self._bm_command_prefix = self._resolve_bm_command_prefix(run_config)
        self._bm_env = self._isolated_bm_env()
        self._status_json_supported = None
        project_name = self._project_name(run_config)

        # Trigger: benchmark corpus needs indexing in BM
        # Why: keep provider path external to BM internals
        # Outcome: project exists, index is updated, and warm MCP session is ready
        add_args = ["project", "add", project_name, str(corpus_path)]
        try:
            self._run_bm(add_args)
        except subprocess.CalledProcessError as exc:
            merged = (exc.stdout or "") + "\n" + (exc.stderr or "")
            merged_lower = merged.lower()
            if "already exists" in merged_lower:
                pass
            else:
                existing = self._extract_existing_project_name(merged)
                if existing is None:
                    raise
                project_name = existing

        self._resolved_project_names[run_config.run_id] = project_name

        try:
            self._run_bm(["reindex", "--search", "--embeddings", "-p", project_name])
        except subprocess.CalledProcessError:
            self._run_bm(["reindex", "--search", "-p", project_name])

        self._wait_for_index_ready(project_name)

        # Trigger: first ingest of this provider instance
        # Why: an MCP session serves projects added after it started, so one
        # warm session covers every group in a grouped run — restarting it
        # per group wastes seconds x hundreds of groups.
        # Outcome: session starts once; cleanup() at end of run stops it.
        if self._mcp is None:
            mcp_command = self._bm_command_prefix[0]
            mcp_args = self._bm_command_prefix[1:] + ["mcp"]
            self._mcp = WarmMcpClient(command=mcp_command, args=mcp_args, env=self._bm_env)
            self._mcp.start()

    @staticmethod
    def _doc_id_from_item(item: dict) -> str | None:
        raw = item.get("source_doc_id") or item.get("permalink") or item.get("file_path")
        if not raw:
            return None
        name = str(raw).rstrip("/").split("/")[-1]
        if name.endswith(".md"):
            name = name[:-3]
        return name

    @classmethod
    def _row_to_hit(cls, row: dict[str, Any]) -> SearchHit:
        """Normalize a search_notes result row into a SearchHit.

        Carries the document title into hit metadata: BM's matched_chunk is a
        single bullet that strips document context, but the title holds it (for
        LoCoMo/LongMemEval the session date lives there, e.g.
        ``locomo-c00-s07 (4:33 pm on 12 July, 2023)``). Surfacing it lets the QA
        assembler anchor relative dates ("two days ago") that bullets alone
        leave unresolvable.
        """
        metadata_raw = row.get("metadata")
        metadata: dict[str, Any]
        if isinstance(metadata_raw, dict):
            metadata = cast(dict[str, Any], metadata_raw)
        else:
            metadata = {}
        if row.get("title"):
            metadata = {**metadata, "title": str(row["title"])}
        return SearchHit(
            id=str(
                row.get("entity_id") or row.get("observation_id") or row.get("relation_id") or ""
            ),
            source_doc_id=cls._doc_id_from_item(row),
            source_path=row.get("file_path") or row.get("permalink"),
            text=row.get("matched_chunk") or row.get("content"),
            score=float(row.get("score", 0.0) or 0.0),
            metadata=metadata,
        )

    def search(self, query: str, limit: int, run_config: RunConfig) -> list[SearchHit]:
        if self._mcp is None:
            raise RuntimeError("bm-local MCP session is not initialized")

        project_name = self._project_name(run_config)
        tool_result = self._mcp.call_tool(
            "search_notes",
            {
                "query": query,
                "project": project_name,
                "page": 1,
                "page_size": limit,
                "search_type": "hybrid",
                "output_format": "json",
            },
        )
        payload = self._payload_from_call_tool_result(tool_result)
        rows = payload.get("results") if isinstance(payload, dict) else []
        hits: list[SearchHit] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            hits.append(self._row_to_hit(row))
        return hits

    def cleanup(self, run_config: RunConfig) -> None:
        _ = run_config
        if self._mcp is None:
            return
        self._mcp.stop()
        self._mcp = None

    def version_info(self) -> dict[str, str]:
        metadata: dict[str, str] = {"bm_transport": "mcp-stdio"}
        if self._status_json_supported is not None:
            metadata["bm_status_json_supported"] = (
                "true" if self._status_json_supported else "false"
            )
        metadata["bm_command"] = " ".join(self._bm_command_prefix)
        try:
            result = self._run_bm(["--version"])
            metadata["bm_version"] = result.stdout.strip()
        except Exception:
            pass
        return metadata
