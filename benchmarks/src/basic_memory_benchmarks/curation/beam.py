"""Agent-curated ingestion for BEAM (#1398 item 4).

Raw mode (``convert beam``) stores each chat session as a note. Curated mode
runs a curator model over the same sessions, in order, and writes knowledge
notes through Basic Memory's canonical write path (``write_note`` /
``edit_note`` on a warm ``bm mcp`` session). The project's notes then become
the group's docs in a dataset with the raw layout, so the existing retrieval,
QA, and BEAM scoring stages run on it unchanged. The per-ability delta
between the two datasets is the measurement.

The curator is blind: it sees the session transcript, the running date
anchor, and an index of the notes it has written so far. It never sees the
probe questions or the ability names.

Retrieval ground truth is a message-to-doc mapping, which curated notes do
not have. The curated ``queries.json`` therefore carries empty ground truth
and ``ingestion_mode: curated``; recall columns read n/a, the nugget scores
are the comparison.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mcp.types import CallToolResult

from basic_memory_benchmarks.bm_runtime import (
    WarmMcpClient,
    isolated_bm_env,
    resolve_bm_command_prefix,
    settle_index,
)
from basic_memory_benchmarks.converters.beam_to_corpus import batch_anchor, clean_message_content
from basic_memory_benchmarks.datasets.beam import BeamConversation, BeamMessage, load_beam_tier
from basic_memory_benchmarks.llm.runners import LLMRunner, LLMRunnerError, create_runner
from basic_memory_benchmarks.utils import run_command, sha256_file, utc_now_iso

NOTE_DIRECTORIES = ("people", "places", "topics", "events", "preferences", "plans")

# The curator's whole instruction set. Fixed for every session and every
# conversation, and hashed into the provenance manifest. Nothing here names a
# probe ability; the abilities are what the notes are later tested on.
CURATOR_PROMPT_TEMPLATE = """\
You maintain a personal knowledge base about the user in this conversation, as markdown notes.
Today's date: {anchor}.

Below is one chat session between the user and an assistant, then the notes that already exist.
Update the knowledge base so that later questions about the user can be answered from the notes alone.

Rules:
- Record facts about the user: people in their life, places, preferences, plans, decisions, events, possessions, health, work, money, and dates.
- One note per entity or topic. Reuse an existing note when one exists (op "append"); otherwise create one (op "create").
- Every observation has a category in brackets and, when the fact is tied to a time, the resolved calendar date. Resolve "today", "yesterday", "last week" against today's date and write the date.
- When a fact changes or contradicts an earlier note, append an observation marked [update] giving the new fact, the date, and what it replaces. Never delete anything.
- Preserve specifics exactly: names, numbers, amounts, dates, and the order in which events happened.
- Link related notes with [[Note Title]] under a Relations heading.
- Directories: {directories}.

Reply with JSON only, no prose, in this shape:
{{"notes": [{{"op": "create", "title": "...", "directory": "...", "gist": "one line", "content": "markdown"}}]}}

For "create", content is the full note: a "# Title" line, a "## Observations" list of "- [category] fact (date)" lines, and a "## Relations" list of "- relation [[Other Note]]" lines.
For "append", content is only the new lines to add: observations and relations, same formats.
At most {max_notes} operations. If nothing in the session is worth recording, reply {{"notes": []}}.

## Session ({anchor})
{transcript}

## Existing notes (title | directory | gist)
{index}
"""

# Cap on the existing-notes index shown to the curator; beyond this the oldest
# entries are elided so the prompt cost stays bounded across ~90 sessions.
MAX_INDEX_CHARS = 6_000


@dataclass(frozen=True)
class CurationConfig:
    input_dir: Path
    output_dir: Path
    model_spec: str
    model_temperature: float | None
    bm_local_path: str | None
    conversations: tuple[str, ...] = ()
    max_conversations: int | None = None
    max_notes_per_session: int = 8
    settle_timeout_seconds: float = 180.0
    tool_timeout_seconds: float = 120.0
    # Conversations are independent (own home, project, MCP session), so they
    # curate in parallel; one conversation is ~80 serial model calls.
    workers: int = 1
    # `bm mcp` loads the embedding model at startup; with several servers
    # starting on a loaded machine that took over 30s and aborted a whole run.
    startup_timeout_seconds: float = 180.0
    # Reuse a group's curation.json when its curator spec and prompt match.
    resume: bool = False


@dataclass(frozen=True)
class NoteOperation:
    op: str  # "create" | "append"
    title: str
    directory: str
    gist: str
    content: str


@dataclass(frozen=True)
class NoteIndexEntry:
    title: str
    directory: str
    gist: str


@dataclass
class ConversationCurationStats:
    conversation_id: str
    group_id: str
    sessions: int = 0
    model_calls: int = 0
    notes_created: int = 0
    notes_appended: int = 0
    notes_dropped: int = 0
    malformed_responses: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_seconds: float = 0.0
    doc_count: int = 0
    error: str | None = None
    docs_sha256: dict[str, str] = field(default_factory=dict)


class NoteWriter(Protocol):
    """The slice of a warm ``bm mcp`` session the curator drives."""

    def start(self) -> None: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult: ...
    def stop(self) -> None: ...


WriterFactory = Callable[[list[str], dict[str, str], Path], NoteWriter]


def make_writer_factory(config: CurationConfig) -> WriterFactory:
    def factory(prefix: list[str], env: dict[str, str], project_dir: Path) -> NoteWriter:
        return WarmMcpClient(
            command=prefix[0],
            args=prefix[1:] + ["mcp"],
            env=env,
            startup_timeout_seconds=config.startup_timeout_seconds,
            request_timeout_seconds=config.tool_timeout_seconds,
            required_tool="write_note",
        )

    return factory


# The per-group record a finished conversation leaves beside its docs, so a
# relaunch can resume instead of paying for the conversation again.
CURATION_RECORD = "curation.json"


# --- Prompt assembly ---


def render_session_transcript(messages: list[BeamMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        content = clean_message_content(message.content)
        if not content:
            continue
        speaker = "User" if message.role == "user" else "Assistant"
        lines.append(f"- **{speaker}:** {content}")
    return "\n".join(lines)


def render_note_index(entries: list[NoteIndexEntry]) -> str:
    if not entries:
        return "(none yet)"
    rows = [f"- {entry.title} | {entry.directory} | {entry.gist}" for entry in entries]
    text = "\n".join(rows)
    if len(text) <= MAX_INDEX_CHARS:
        return text
    # Keep the newest entries: they are the ones a session is most likely to
    # extend, and the elision is stated so the curator knows the list is partial.
    kept: list[str] = []
    size = 0
    for row in reversed(rows):
        if size + len(row) + 1 > MAX_INDEX_CHARS:
            break
        kept.append(row)
        size += len(row) + 1
    kept.reverse()
    return f"(… {len(rows) - len(kept)} older notes not shown)\n" + "\n".join(kept)


def build_curator_prompt(
    *, anchor: str | None, transcript: str, index: list[NoteIndexEntry], max_notes: int
) -> str:
    return CURATOR_PROMPT_TEMPLATE.format(
        anchor=anchor or "unknown",
        directories=", ".join(NOTE_DIRECTORIES),
        max_notes=max_notes,
        transcript=transcript,
        index=render_note_index(index),
    )


def prompt_sha256() -> str:
    return hashlib.sha256(CURATOR_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


# --- Response parsing ---


def parse_note_operations(text: str) -> list[NoteOperation]:
    """Parse the curator's JSON reply. Raises ValueError on any shape problem."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
        raise ValueError("curator reply must be an object with a 'notes' list")
    operations: list[NoteOperation] = []
    for raw in payload["notes"]:
        if not isinstance(raw, dict):
            raise ValueError("each note operation must be an object")
        op = raw.get("op")
        if op not in ("create", "append"):
            raise ValueError(f"unknown note op {op!r}")
        title = raw.get("title")
        content = raw.get("content")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("note operation needs a non-empty title")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"note operation for {title!r} needs non-empty content")
        raw_directory = raw.get("directory")
        directory = raw_directory if raw_directory in NOTE_DIRECTORIES else "topics"
        raw_gist = raw.get("gist")
        gist = raw_gist.strip()[:160] if isinstance(raw_gist, str) else ""
        operations.append(
            NoteOperation(
                op=op,
                title=title.strip(),
                directory=directory,
                gist=gist,
                content=content.strip(),
            )
        )
    return operations


# --- Applying operations ---


def apply_note_operation(
    writer: NoteWriter,
    *,
    project: str,
    operation: NoteOperation,
    index: dict[str, NoteIndexEntry],
    stats: ConversationCurationStats,
) -> None:
    # An "append" to a note that does not exist yet is a create: the curator's
    # index view can lag by one session when the prompt index was elided.
    if operation.op == "append" and operation.title in index:
        result = writer.call_tool(
            "edit_note",
            {
                "identifier": operation.title,
                "operation": "append",
                "content": "\n" + operation.content + "\n",
                "project": project,
            },
        )
        if result.isError:
            stats.tool_errors += 1
            return
        stats.notes_appended += 1
        if operation.gist:
            existing = index[operation.title]
            index[operation.title] = NoteIndexEntry(
                existing.title, existing.directory, operation.gist
            )
        return
    if operation.op == "create" and operation.title in index:
        # A second create for a known title is an update in disguise; append
        # the new body rather than overwrite what earlier sessions recorded.
        result = writer.call_tool(
            "edit_note",
            {
                "identifier": operation.title,
                "operation": "append",
                "content": "\n" + operation.content + "\n",
                "project": project,
            },
        )
        if result.isError:
            stats.tool_errors += 1
            return
        stats.notes_appended += 1
        return
    result = writer.call_tool(
        "write_note",
        {
            "title": operation.title,
            "directory": operation.directory,
            "content": operation.content,
            "project": project,
            "note_type": "note",
        },
    )
    if result.isError:
        stats.tool_errors += 1
        return
    stats.notes_created += 1
    index[operation.title] = NoteIndexEntry(operation.title, operation.directory, operation.gist)


def curate_session(
    *,
    runner: LLMRunner,
    writer: NoteWriter,
    project: str,
    messages: list[BeamMessage],
    anchor: str | None,
    index: dict[str, NoteIndexEntry],
    stats: ConversationCurationStats,
    max_notes: int,
) -> None:
    transcript = render_session_transcript(messages)
    if not transcript:
        return
    prompt = build_curator_prompt(
        anchor=anchor, transcript=transcript, index=list(index.values()), max_notes=max_notes
    )
    result = runner.complete(prompt)
    stats.model_calls += 1
    stats.input_tokens += result.input_tokens
    stats.output_tokens += result.output_tokens
    try:
        operations = parse_note_operations(result.text)
    except (ValueError, json.JSONDecodeError):
        # A malformed reply costs one session's facts, not the conversation:
        # counted, and visible in the manifest as a quality signal.
        stats.malformed_responses += 1
        return
    for operation in operations[:max_notes]:
        apply_note_operation(writer, project=project, operation=operation, index=index, stats=stats)
    stats.notes_dropped += max(0, len(operations) - max_notes)


def curate_conversation(
    conversation: BeamConversation,
    *,
    group_id: str,
    chat_sha256: str,
    config: CurationConfig,
    prefix: list[str],
    runner: LLMRunner,
    writer_factory: WriterFactory,
) -> ConversationCurationStats:
    """Curate one conversation into a fresh project and copy its notes out."""
    started = time.monotonic()
    stats = ConversationCurationStats(
        conversation_id=conversation.conversation_id, group_id=group_id
    )
    home = config.output_dir / ".bm-homes" / group_id
    if home.exists():
        shutil.rmtree(home)
    project_dir = home / "project"
    project_dir.mkdir(parents=True)
    (home / "default-home").mkdir(parents=True)
    env = isolated_bm_env(home)
    writer = writer_factory(prefix, env, project_dir)
    try:
        run_command(prefix + ["project", "add", group_id, str(project_dir)], env=env)
        writer.start()
    except (subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        # The project could not be registered or the server did not come up
        # (a loaded machine starting several servers at once). One
        # conversation is lost and recorded; the others keep running.
        stats.error = f"bm setup failed: {exc}"
        stats.wall_seconds = round(time.monotonic() - started, 2)
        return stats
    index: dict[str, NoteIndexEntry] = {}
    current_anchor: str | None = None
    try:
        for batch in conversation.batches:
            batch_messages = [message for session in batch.turns for message in session]
            anchor = batch_anchor(batch_messages, batch.time_anchor)
            if anchor:
                current_anchor = anchor
            for session in batch.turns:
                stats.sessions += 1
                curate_session(
                    runner=runner,
                    writer=writer,
                    project=group_id,
                    messages=session,
                    anchor=current_anchor,
                    index=index,
                    stats=stats,
                    max_notes=config.max_notes_per_session,
                )
    except LLMRunnerError as exc:
        # The curator's transport died mid-conversation. A half-curated
        # project would score as a real result, so the conversation is
        # excluded, with the reason recorded, and the run continues.
        stats.error = f"curator failed after {stats.sessions} sessions: {exc}"
    finally:
        writer.stop()

    if stats.error is None:
        run_command(prefix + ["reindex", "-p", group_id, "--search"], env=env)
        settle_index(
            prefix=prefix,
            env=env,
            project_name=group_id,
            timeout_seconds=config.settle_timeout_seconds,
        )
        docs_dir = config.output_dir / "groups" / group_id / "docs"
        if docs_dir.exists():
            shutil.rmtree(docs_dir)
        docs_dir.mkdir(parents=True)
        for note_path in sorted(project_dir.rglob("*.md")):
            relative = note_path.relative_to(project_dir)
            target = docs_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(note_path, target)
            stats.docs_sha256[relative.as_posix()] = sha256_file(target)
        stats.doc_count = len(stats.docs_sha256)
    stats.wall_seconds = round(time.monotonic() - started, 2)
    if stats.error is None:
        write_curation_record(config, stats, chat_sha256=chat_sha256)
    return stats


def resume_key(config: CurationConfig, chat_sha256: str) -> dict[str, Any]:
    """Every input that changes a group's curated output.

    A record is reusable only when all of these match the current run: the
    curator and its sampling temperature, the prompt, the note cap, and the
    exact source chat the conversation was curated from. Anything else would
    let a rebuilt manifest advertise settings its docs were not produced under.
    """
    return {
        "curator_model_spec": config.model_spec,
        "curator_temperature": config.model_temperature,
        "curator_prompt_sha256": prompt_sha256(),
        "max_notes_per_session": config.max_notes_per_session,
        "chat_sha256": chat_sha256,
    }


def write_curation_record(
    config: CurationConfig, stats: ConversationCurationStats, *, chat_sha256: str
) -> None:
    record = {"key": resume_key(config, chat_sha256), "stats": asdict(stats)}
    path = config.output_dir / "groups" / stats.group_id / CURATION_RECORD
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def load_curation_record(
    config: CurationConfig, group_id: str, *, chat_sha256: str
) -> ConversationCurationStats | None:
    """A finished group's stats, only when its resume key matches this run exactly."""
    path = config.output_dir / "groups" / group_id / CURATION_RECORD
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("key") != resume_key(config, chat_sha256):
        return None
    docs_dir = config.output_dir / "groups" / group_id / "docs"
    stats = ConversationCurationStats(**record["stats"])
    for relative, digest in stats.docs_sha256.items():
        doc = docs_dir / relative
        if not doc.exists() or sha256_file(doc) != digest:
            return None
    return stats


# --- Dataset assembly ---


def _load_input_manifest(input_dir: Path) -> dict[str, Any]:
    manifest_path = input_dir / "conversion.json"
    if not manifest_path.exists():
        raise ValueError(f"no conversion.json under {input_dir}; run `convert beam` first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("converter", {}).get("mode") != "raw":
        raise ValueError(f"{manifest_path} is not a raw BEAM conversion")
    return manifest


def _select_conversations(
    conversations: list[BeamConversation], config: CurationConfig
) -> list[BeamConversation]:
    if config.conversations:
        wanted = set(config.conversations)
        selected = [c for c in conversations if c.conversation_id in wanted]
        missing = sorted(wanted - {c.conversation_id for c in selected})
        if missing:
            raise ValueError(f"unknown BEAM conversations {missing}")
    else:
        selected = list(conversations)
    if config.max_conversations is not None:
        selected = selected[: config.max_conversations]
    return selected


def curated_queries(raw_queries: list[dict[str, Any]], group_ids: set[str]) -> list[dict[str, Any]]:
    """The raw queries for the curated groups, with retrieval ground truth withdrawn."""
    queries: list[dict[str, Any]] = []
    for query in raw_queries:
        if query["group"] not in group_ids:
            continue
        rewritten = dict(query)
        metadata = dict(query.get("metadata", {}))
        metadata["ingestion_mode"] = "curated"
        metadata["raw_ground_truth"] = list(query.get("ground_truth", []))
        rewritten["ground_truth"] = []
        rewritten["metadata"] = metadata
        queries.append(rewritten)
    return queries


def curate_beam(
    config: CurationConfig,
    *,
    runner: LLMRunner | None = None,
    writer_factory: WriterFactory | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> Path:
    """Curate a raw-converted BEAM tier into a sibling curated dataset. Returns output_dir."""
    manifest = _load_input_manifest(config.input_dir)
    dataset_root = Path(manifest["dataset_root"])
    tier = str(manifest["tier"])
    raw_dataset_id = str(manifest["dataset_id"])
    dataset_id = f"{raw_dataset_id}-curated"
    raw_queries = json.loads((config.input_dir / "queries.json").read_text(encoding="utf-8"))
    chat_sha_by_conversation = {
        str(row["conversation_id"]): str(row["chat_sha256"]) for row in manifest["conversations"]
    }

    conversations = _select_conversations(load_beam_tier(dataset_root, tier), config)
    prefix = resolve_bm_command_prefix(config.bm_local_path)
    curator = runner or create_runner(config.model_spec, temperature=config.model_temperature)
    factory = writer_factory or make_writer_factory(config)

    config.output_dir.mkdir(parents=True, exist_ok=True)

    def curate_one(conversation: BeamConversation) -> ConversationCurationStats:
        # Same group ids as the raw dataset, so query ids line up row for row.
        group_id = f"{raw_dataset_id}-c{int(conversation.conversation_id):02d}"
        chat_sha256 = chat_sha_by_conversation[conversation.conversation_id]
        if config.resume:
            finished = load_curation_record(config, group_id, chat_sha256=chat_sha256)
            if finished is not None:
                progress(f"resuming {group_id}: {finished.doc_count} docs already curated")
                return finished
        progress(f"curating {group_id} ({len(conversation.batches)} batches)")
        stats = curate_conversation(
            conversation,
            group_id=group_id,
            chat_sha256=chat_sha256,
            config=config,
            prefix=prefix,
            runner=curator,
            writer_factory=factory,
        )
        progress(
            f"  {group_id}: sessions={stats.sessions} notes={stats.notes_created}+{stats.notes_appended} "
            f"tokens={stats.input_tokens}+{stats.output_tokens} "
            f"malformed={stats.malformed_responses} tool_errors={stats.tool_errors} "
            f"wall={stats.wall_seconds}s" + (f" ERROR: {stats.error}" if stats.error else "")
        )
        return stats

    # Manifest order is dataset order whatever the completion order was.
    if config.workers > 1:
        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            stats_by_group = list(pool.map(curate_one, conversations))
    else:
        stats_by_group = [curate_one(conversation) for conversation in conversations]

    curated_groups = {stats.group_id for stats in stats_by_group if stats.error is None}
    queries = curated_queries(raw_queries, curated_groups)
    (config.output_dir / "queries.json").write_text(json.dumps(queries, indent=2), encoding="utf-8")

    conversion_manifest = {
        "dataset_id": dataset_id,
        "tier": tier,
        "source_url": manifest.get("source_url"),
        "citation": manifest.get("citation"),
        "license_note": manifest.get("license_note"),
        "dataset_root": str(dataset_root),
        "converter": {
            "mode": "curated",
            "raw_input_dir": str(config.input_dir),
            "raw_conversion_sha256": sha256_file(config.input_dir / "conversion.json"),
            "curator_model_spec": config.model_spec,
            "curator_temperature": config.model_temperature,
            "curator_prompt_sha256": prompt_sha256(),
            "max_notes_per_session": config.max_notes_per_session,
            "conversations": list(config.conversations) or None,
            "max_conversations": config.max_conversations,
            "workers": config.workers,
            "startup_timeout_seconds": config.startup_timeout_seconds,
            "resume": config.resume,
        },
        "created_at_utc": utc_now_iso(),
        "conversations": [asdict(stats) for stats in stats_by_group],
        "excluded_conversations": [
            {"group_id": stats.group_id, "error": stats.error}
            for stats in stats_by_group
            if stats.error is not None
        ],
        "totals": {
            "conversations": len(curated_groups),
            "sessions": sum(s.sessions for s in stats_by_group),
            "model_calls": sum(s.model_calls for s in stats_by_group),
            "input_tokens": sum(s.input_tokens for s in stats_by_group),
            "output_tokens": sum(s.output_tokens for s in stats_by_group),
            "docs": sum(s.doc_count for s in stats_by_group),
            "queries": len(queries),
        },
    }
    (config.output_dir / "conversion.json").write_text(
        json.dumps(conversion_manifest, indent=2), encoding="utf-8"
    )
    return config.output_dir
