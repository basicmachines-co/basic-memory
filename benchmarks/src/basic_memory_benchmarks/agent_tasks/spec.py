"""Declarative task-spec and grader types for the agent-task eval.

Specs are pure frozen data (house style: data before behavior); evaluation
lives in ``grading.py``. A task passes iff every ``required=True`` predicate
passes; non-required predicates (e.g. ``ToolCalled``) are diagnostics only.
"""

from __future__ import annotations

from dataclasses import dataclass

# A file selector: a glob relative to the project dir, or the sentinel "new"
# meaning "the file(s) added since the baseline snapshot".
type PathSel = str

NEW_FILE_SELECTOR = "new"


@dataclass(frozen=True)
class AnswerSetEquals:
    """The final answer's fenced-JSON ``key`` list equals ``gold`` as a set."""

    key: str
    gold: frozenset[str]
    required: bool = True


@dataclass(frozen=True)
class MarkerPresent:
    marker: str
    required: bool = True


@dataclass(frozen=True)
class MarkerAbsent:
    marker: str
    required: bool = True


@dataclass(frozen=True)
class AnswerContains:
    needle: str
    required: bool = True


@dataclass(frozen=True)
class AnswerMatches:
    """The final answer matches ``pattern`` (case-insensitive, like AnswerContains)."""

    pattern: str
    required: bool = True


@dataclass(frozen=True)
class NoteCountDelta:
    """Markdown file count changed by exactly ``delta`` vs the baseline."""

    delta: int
    required: bool = True


@dataclass(frozen=True)
class NewNoteUnder:
    """Every file added since the baseline snapshot lives under ``prefix``."""

    prefix: str
    required: bool = True


@dataclass(frozen=True)
class NotesUntouched:
    """Every baseline file is byte-identical, except paths matching the globs."""

    except_globs: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class FrontmatterEquals:
    """Dot-notation frontmatter key equals ``value`` in the selected file."""

    path: PathSel
    key: str
    value: str | int | float | bool
    required: bool = True


@dataclass(frozen=True)
class FrontmatterMatches:
    path: PathSel
    key: str
    pattern: str
    required: bool = True


@dataclass(frozen=True)
class FrontmatterListLen:
    path: PathSel
    key: str
    length: int
    required: bool = True


@dataclass(frozen=True)
class ObservationLines:
    """At least ``min_count`` lines matching ``pattern`` in the selected file."""

    path: PathSel
    pattern: str
    min_count: int
    required: bool = True


@dataclass(frozen=True)
class RelationResolves:
    """A resolved relation row exists from source to one of ``targets`` in the DB."""

    source_permalink: str
    targets: frozenset[str]
    relation_type: str | None = None
    required: bool = True


@dataclass(frozen=True)
class FileLineDiff:
    """Exactly one line changed vs baseline, matching the given patterns."""

    path: PathSel
    removed_pattern: str
    added_pattern: str
    required: bool = True


@dataclass(frozen=True)
class ToolCalled:
    """Diagnostic: some dispatched tool call's name matches ``name_pattern``."""

    name_pattern: str
    required: bool = False


@dataclass(frozen=True)
class JudgeRubric:
    """LLM-judged rubric over the final answer (via the package judge seam)."""

    rubric: str
    required: bool = True


type Grader = (
    AnswerSetEquals
    | MarkerPresent
    | MarkerAbsent
    | AnswerContains
    | AnswerMatches
    | NoteCountDelta
    | NewNoteUnder
    | NotesUntouched
    | FrontmatterEquals
    | FrontmatterMatches
    | FrontmatterListLen
    | ObservationLines
    | RelationResolves
    | FileLineDiff
    | ToolCalled
    | JudgeRubric
)


@dataclass(frozen=True)
class AgentTaskSpec:
    id: str
    # "memory-continue" | "memory-curate" | "memory-metadata-search" |
    # "memory-tasks" | "manual"
    skill: str
    # Attribution, e.g. "skills/memory-continue/SKILL.md" or "SPEC-47 manual chain".
    source: str
    prompt: str
    graders: tuple[Grader, ...]
