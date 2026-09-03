"""Curated BEAM ingestion: blind curator, canonical write path, raw layout out."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

import basic_memory_benchmarks.curation.beam as curation
from basic_memory_benchmarks.converters.beam_to_corpus import convert_beam_to_corpus
from basic_memory_benchmarks.curation.beam import (
    CURATOR_PROMPT_TEMPLATE,
    MAX_INDEX_CHARS,
    CurationConfig,
    NoteIndexEntry,
    curate_beam,
    parse_note_operations,
    render_note_index,
)
from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner, LLMRunnerError
from beam_fixture import write_beam_tier


class ScriptedRunner(LLMRunner):
    """Returns canned replies in order; a callable reply raises instead."""

    spec = "scripted:curator"

    def __init__(self, replies: list[str | Callable[[], str]]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else '{"notes": []}'
        text = reply() if callable(reply) else reply
        return LLMResult(
            text=text, model="scripted", input_tokens=100, output_tokens=10, latency_ms=1.0
        )


class FileWriter:
    """Stands in for the warm `bm mcp` session: writes notes as files in the project."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, dict(arguments)))
        if name == "write_note":
            path = self.project_dir / arguments["directory"] / f"{arguments['title']}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\ntitle: {arguments['title']}\n---\n{arguments['content']}\n")
            return CallToolResult(content=[], isError=False)
        if name == "edit_note":
            matches = list(self.project_dir.rglob(f"{arguments['identifier']}.md"))
            if not matches:
                return CallToolResult(content=[], isError=True)
            with matches[0].open("a") as handle:
                handle.write(arguments["content"])
            return CallToolResult(content=[], isError=False)
        raise AssertionError(f"unexpected tool {name}")

    def stop(self) -> None:
        self.stopped = True


def _raw_dataset(tmp_path: Path) -> Path:
    chats_root = write_beam_tier(tmp_path / "chats")
    raw_dir = tmp_path / "raw"
    convert_beam_to_corpus(dataset_root=chats_root, output_dir=raw_dir, tier="100K")
    return raw_dir


def _config(tmp_path: Path, **overrides: Any) -> CurationConfig:
    values: dict[str, Any] = {
        "input_dir": tmp_path / "raw",
        "output_dir": tmp_path / "curated",
        "model_spec": "scripted:curator",
        "model_temperature": None,
        "bm_local_path": None,
    }
    values.update(overrides)
    return CurationConfig(**values)


def _stub_bm(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[str]]:
    commands: list[list[str]] = []
    settles: list[str] = []
    monkeypatch.setattr(curation, "resolve_bm_command_prefix", lambda bm_local_path: ["bm"])

    def record_command(command: list[str], **kwargs: Any) -> None:
        commands.append(list(command))

    def record_settle(
        *, prefix: list[str], env: dict[str, str], project_name: str, timeout_seconds: float
    ):
        settles.append(project_name)
        return (0.0, "status-json")

    monkeypatch.setattr(curation, "run_command", record_command)
    monkeypatch.setattr(curation, "settle_index", record_settle)
    return commands, settles


def _ops(*notes: dict[str, Any]) -> str:
    return json.dumps({"notes": list(notes)})


# --- parsing ---


def test_parse_note_operations_accepts_fenced_json_and_normalizes_directory() -> None:
    text = '```json\n{"notes": [{"op": "create", "title": " Alice ", "directory": "nowhere", "gist": "friend", "content": "# Alice\\n## Observations\\n- [role] friend"}]}\n```'
    (operation,) = parse_note_operations(text)
    assert operation.title == "Alice"
    assert operation.directory == "topics"
    assert operation.gist == "friend"


@pytest.mark.parametrize(
    "text",
    [
        '{"notes": [{"op": "delete", "title": "Alice", "content": "x"}]}',
        '{"notes": [{"op": "create", "title": "", "content": "x"}]}',
        '{"notes": [{"op": "create", "title": "Alice", "content": ""}]}',
        '{"items": []}',
        "[]",
    ],
)
def test_parse_note_operations_rejects_bad_shapes(text: str) -> None:
    with pytest.raises(ValueError):
        parse_note_operations(text)


def test_render_note_index_elides_oldest_entries_past_the_cap() -> None:
    entries = [NoteIndexEntry(f"Note {i:04d}", "topics", "x" * 100) for i in range(200)]
    text = render_note_index(entries)
    assert len(text) <= MAX_INDEX_CHARS + 60
    assert text.startswith("(… ")
    assert "Note 0199" in text
    assert "Note 0000" not in text


def test_prompt_never_names_probe_abilities() -> None:
    lowered = CURATOR_PROMPT_TEMPLATE.lower()
    for ability in (
        "contradiction",
        "temporal reasoning",
        "knowledge update",
        "abstention",
        "event ordering",
    ):
        assert ability not in lowered


# --- end to end on the fixture tier ---


def test_curate_beam_writes_notes_through_the_writer_and_emits_the_raw_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raw_dataset(tmp_path)
    commands, settles = _stub_bm(monkeypatch)
    writers: dict[Path, FileWriter] = {}

    def writer_factory(prefix: list[str], env: dict[str, str], project_dir: Path) -> FileWriter:
        writers[project_dir] = FileWriter(project_dir)
        return writers[project_dir]

    runner = ScriptedRunner(
        [
            _ops(
                {
                    "op": "create",
                    "title": "Alice",
                    "directory": "people",
                    "gist": "sister",
                    "content": "# Alice\n## Observations\n- [relationship] sister (2024-03-15)",
                }
            ),
            _ops(
                {
                    "op": "append",
                    "title": "Alice",
                    "directory": "people",
                    "gist": "moved",
                    "content": "- [update] moved to Paris (2024-03-20)",
                },
                {
                    "op": "create",
                    "title": "Paris",
                    "directory": "places",
                    "gist": "city",
                    "content": "# Paris\n## Relations\n- home_of [[Alice]]",
                },
            ),
            "this is not json",
        ]
    )

    output = curate_beam(_config(tmp_path), runner=runner, writer_factory=writer_factory)

    manifest = json.loads((output / "conversion.json").read_text())
    assert manifest["dataset_id"] == "beam-100k-curated"
    assert manifest["converter"]["mode"] == "curated"
    assert manifest["converter"]["curator_prompt_sha256"]
    assert manifest["excluded_conversations"] == []
    stats = {row["group_id"]: row for row in manifest["conversations"]}
    first = stats["beam-100k-c01"]
    assert first["notes_created"] == 2
    assert first["notes_appended"] == 1
    assert first["malformed_responses"] == 1
    assert first["error"] is None
    assert first["model_calls"] == first["sessions"]

    # Notes reached the project through write_note / edit_note and were copied out.
    alice = output / "groups" / "beam-100k-c01" / "docs" / "people" / "Alice.md"
    assert alice.exists()
    assert "moved to Paris" in alice.read_text()
    assert (output / "groups" / "beam-100k-c01" / "docs" / "places" / "Paris.md").exists()
    assert first["docs_sha256"] and set(first["docs_sha256"]) == {
        "people/Alice.md",
        "places/Paris.md",
    }

    # Each group: one project add, one reindex, one settle; the writer was started and stopped.
    adds = [cmd for cmd in commands if "add" in cmd]
    assert sorted(cmd[cmd.index("add") + 1] for cmd in adds) == ["beam-100k-c01", "beam-100k-c02"]
    assert sorted(settles) == ["beam-100k-c01", "beam-100k-c02"]
    assert all(writer.started and writer.stopped for writer in writers.values())

    # Queries keep the raw ids and withdraw retrieval ground truth.
    raw_queries = json.loads((tmp_path / "raw" / "queries.json").read_text())
    queries = json.loads((output / "queries.json").read_text())
    assert [q["id"] for q in queries] == [q["id"] for q in raw_queries]
    assert all(q["ground_truth"] == [] for q in queries)
    assert all(q["metadata"]["ingestion_mode"] == "curated" for q in queries)
    assert any(q["metadata"]["raw_ground_truth"] for q in queries)

    # The curator saw the session, the date, and its own index; never a probe.
    assert any("Existing notes" in prompt for prompt in runner.prompts)
    assert all("probe" not in prompt.lower() for prompt in runner.prompts)


def test_a_curator_transport_failure_excludes_that_conversation_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)

    def boom() -> str:
        raise LLMRunnerError("endpoint down")

    # Conversation 1 gets an empty reply per session; conversation 2's first call dies.
    replies: list[str | Callable[[], str]] = []
    raw_manifest = json.loads((tmp_path / "raw" / "conversion.json").read_text())
    assert [row["conversation_id"] for row in raw_manifest["conversations"]] == ["1", "2"]
    from basic_memory_benchmarks.datasets.beam import load_beam_tier

    sessions_one = sum(len(b.turns) for b in load_beam_tier(tmp_path / "chats", "100K")[0].batches)
    replies.extend(['{"notes": []}'] * sessions_one)
    replies.append(boom)
    runner = ScriptedRunner(replies)

    output = curate_beam(
        _config(tmp_path), runner=runner, writer_factory=lambda p, e, d: FileWriter(d)
    )

    manifest = json.loads((output / "conversion.json").read_text())
    assert [row["group_id"] for row in manifest["excluded_conversations"]] == ["beam-100k-c02"]
    assert "endpoint down" in manifest["excluded_conversations"][0]["error"]
    assert not (output / "groups" / "beam-100k-c02").exists()
    queries = json.loads((output / "queries.json").read_text())
    assert {q["group"] for q in queries} == {"beam-100k-c01"}
    assert manifest["totals"]["conversations"] == 1


def test_append_to_unknown_title_creates_and_create_of_known_title_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)
    writer_holder: list[FileWriter] = []

    def writer_factory(prefix: list[str], env: dict[str, str], project_dir: Path) -> FileWriter:
        writer = FileWriter(project_dir)
        writer_holder.append(writer)
        return writer

    runner = ScriptedRunner(
        [
            _ops(
                {
                    "op": "append",
                    "title": "Bob",
                    "directory": "people",
                    "gist": "",
                    "content": "- [role] colleague",
                }
            ),
            _ops(
                {
                    "op": "create",
                    "title": "Bob",
                    "directory": "people",
                    "gist": "",
                    "content": "# Bob\n- [role] manager now",
                }
            ),
        ]
    )

    curate_beam(
        _config(tmp_path, conversations=("1",)), runner=runner, writer_factory=writer_factory
    )

    names = [name for name, _ in writer_holder[0].calls]
    assert names[:2] == ["write_note", "edit_note"]


def test_unknown_conversation_ids_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)
    with pytest.raises(ValueError, match="unknown BEAM conversations"):
        curate_beam(
            _config(tmp_path, conversations=("9",)),
            runner=ScriptedRunner([]),
            writer_factory=lambda p, e, d: FileWriter(d),
        )


def test_workers_curate_in_parallel_and_keep_dataset_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two conversations, two workers: same manifest as serial, in dataset order."""
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)
    import threading

    lock = threading.Lock()
    seen_threads: set[int] = set()

    class ThreadAwareRunner(ScriptedRunner):
        def complete(self, prompt: str) -> LLMResult:
            with lock:
                seen_threads.add(threading.get_ident())
            return super().complete(prompt)

    output = curate_beam(
        _config(tmp_path, workers=2),
        runner=ThreadAwareRunner([]),
        writer_factory=lambda p, e, d: FileWriter(d),
    )

    manifest = json.loads((output / "conversion.json").read_text())
    assert [row["group_id"] for row in manifest["conversations"]] == [
        "beam-100k-c01",
        "beam-100k-c02",
    ]
    assert manifest["converter"]["workers"] == 2
    assert manifest["totals"]["conversations"] == 2
    assert len(seen_threads) == 2


def test_a_server_that_fails_to_start_excludes_that_conversation_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first full run died on `Timed out starting bm mcp session` from one
    conversation on a loaded machine; the whole run aborted with no manifest."""
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)

    class DeadWriter(FileWriter):
        def start(self) -> None:
            raise TimeoutError("Timed out starting bm mcp session")

    def writer_factory(prefix: list[str], env: dict[str, str], project_dir: Path) -> FileWriter:
        return (
            DeadWriter(project_dir)
            if project_dir.parent.name.endswith("c02")
            else FileWriter(project_dir)
        )

    output = curate_beam(
        _config(tmp_path), runner=ScriptedRunner([]), writer_factory=writer_factory
    )

    manifest = json.loads((output / "conversion.json").read_text())
    assert [row["group_id"] for row in manifest["excluded_conversations"]] == ["beam-100k-c02"]
    assert "bm setup failed" in manifest["excluded_conversations"][0]["error"]
    assert manifest["totals"]["conversations"] == 1
    assert (output / "groups" / "beam-100k-c01" / "curation.json").exists()
    assert not (output / "groups" / "beam-100k-c02" / "curation.json").exists()


def test_resume_reuses_finished_groups_and_recurates_on_prompt_or_model_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _raw_dataset(tmp_path)
    _stub_bm(monkeypatch)
    first = ScriptedRunner([])
    curate_beam(_config(tmp_path), runner=first, writer_factory=lambda p, e, d: FileWriter(d))
    calls_first = len(first.prompts)
    assert calls_first > 0

    # Same curator and prompt: nothing is re-run, the manifest is rebuilt from records.
    second = ScriptedRunner([])
    output = curate_beam(
        _config(tmp_path, resume=True), runner=second, writer_factory=lambda p, e, d: FileWriter(d)
    )
    assert second.prompts == []
    manifest = json.loads((output / "conversion.json").read_text())
    assert manifest["totals"]["conversations"] == 2
    assert manifest["converter"]["resume"] is True

    # A different curator spec invalidates the records: everything is curated again.
    third = ScriptedRunner([])
    curate_beam(
        _config(tmp_path, resume=True, model_spec="scripted:other"),
        runner=third,
        writer_factory=lambda p, e, d: FileWriter(d),
    )
    assert len(third.prompts) == calls_first
