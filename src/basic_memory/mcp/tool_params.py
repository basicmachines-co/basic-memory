"""Shared parameter types for the bm_* MCP tools (SPEC-35.2, #1829).

Every bm_* tool names its target, project, time window, paging and output the same
way. Each type here carries the schema description a model reads, the hidden aliases
older callers and host tools reach for, and the validation that runs before a tool
body sees the value. Tools import these instead of declaring their own variants, so
one parameter means one thing across the surface.
"""

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.schemas.base import validate_timeframe
from basic_memory.schemas.search import SearchItemType
from basic_memory.utils import coerce_dict, parse_str_list

# --- Targets ---

NoteIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        # `file_path` is the spelling Claude Code's built-in Read/Write/Edit tools use;
        # accepting it keeps a model's habit from turning into a validation error.
        validation_alias=AliasChoices("identifier", "file_path"),
        description=(
            "The note or file: a file path ('notes/plan.md'), permalink, memory:// URL, "
            "exact title, external id, or '<project>/path'."
        ),
    ),
]

DirectoryPath = Annotated[
    str,
    Field(
        # list_directory's `dir_name`, and the word models most often reach for.
        validation_alias=AliasChoices("path", "dir_name", "folder"),
        description=(
            "A directory in the project, e.g. 'specs' or 'specs/active'. '/' is the "
            "project root; '<project>/path' addresses another project."
        ),
    ),
]

# --- Project ---

ProjectParam = Annotated[
    str | None,
    Field(
        # `project_id` stays accepted for callers written against the old tools; a
        # UUID in either spelling routes by external id (see parse_project).
        validation_alias=AliasChoices("project", "project_id"),
        description=(
            "Project name, 'workspace/project', or project external id. Omit to use the "
            "default project."
        ),
    ),
]


@dataclass(frozen=True, slots=True)
class ProjectByName:
    """A project addressed by name, optionally workspace-qualified ('team/specs')."""

    name: str


@dataclass(frozen=True, slots=True)
class ProjectById:
    """A project addressed by its external id, unambiguous across workspaces."""

    external_id: str


type ProjectSelector = ProjectByName | ProjectById | None


def parse_project(value: str | None) -> ProjectSelector:
    """Classify a `project` argument once, at the tool boundary.

    The routing layer takes a name and an external id as separate arguments and gives
    the id precedence. Classifying here keeps a caller from ever supplying both.
    """
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    try:
        return ProjectById(str(UUID(candidate)))
    except ValueError:
        return ProjectByName(candidate)


def routing_arguments(selector: ProjectSelector) -> tuple[str | None, str | None]:
    """The (project, project_id) pair the existing routing functions take."""
    match selector:
        case None:
            return None, None
        case ProjectByName(name=name):
            return name, None
        case ProjectById(external_id=external_id):
            return None, external_id


# --- Time ---

Since = Annotated[
    str,
    BeforeValidator(validate_timeframe),
    Field(
        validation_alias=AliasChoices("since", "timeframe", "after_date"),
        description=(
            "How far back to look: '7d', '24h', '2 days ago', 'last week', or a date. "
            "Windows shorter than one day are widened to one day."
        ),
    ),
]

# --- Paging ---

MAX_PAGE_SIZE = 100

Page = Annotated[
    int,
    Field(ge=1, validation_alias=AliasChoices("page", "page_number"), description="Page number."),
]

PageSize = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_PAGE_SIZE,
        validation_alias=AliasChoices("page_size", "limit", "per_page"),
        description=f"Results per page (1-{MAX_PAGE_SIZE}).",
    ),
]

# --- Output ---

type OutputFormat = Literal["text", "json"]

# --- Filters and item kinds ---

FILTERS_DESCRIPTION = (
    "Frontmatter filters as {field: condition}. Nested fields use dot notation "
    '("schema.confidence"). A condition is a value for equality ({"status": "draft"}), '
    'null for absent-or-null ({"owner": null}), a list for contains-all ({"tags": ["a", "b"]}), '
    "or an operator object with $gt, $gte, $lt, $lte, $in, $between, $contains or $exists, "
    'e.g. {"priority": {"$in": ["high"]}}. Several operators on one field AND together.'
)

Filters = Annotated[
    dict[str, Any] | None,
    # Some MCP clients serialize objects as JSON strings.
    BeforeValidator(coerce_dict),
    Field(
        validation_alias=AliasChoices("filters", "metadata_filters"),
        description=FILTERS_DESCRIPTION,
    ),
]

type ItemKind = Literal["note", "observation", "relation"]


def _parse_item_kinds(value: Any) -> Any:
    """Split list-shaped input and read the API's word "entity" as "note".

    Callers written against `search_notes(entity_types=...)` send "entity"; the alias
    alone would carry that value into a field that no longer accepts it.
    """
    if value is None:
        return None
    # Some MCP clients send the list as a JSON string ('["note", null]').
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            # Malformed JSON stays a string, which validation rejects with a message.
            return value
    # A null member is not a kind; the shared parser would drop it and leave the
    # default search, so the list is handed to validation as given.
    if isinstance(value, list) and any(item is None for item in value):
        return value
    parsed = parse_str_list(value)
    # Non-list input comes back unchanged so Pydantic rejects it with a clear message.
    if not isinstance(parsed, list):
        return parsed
    return ["note" if item == "entity" else item for item in parsed]


Items = Annotated[
    list[ItemKind] | None,
    # Accepts a list, "note,observation", or a JSON-array string.
    BeforeValidator(_parse_item_kinds),
    Field(
        validation_alias=AliasChoices("items", "entity_types"),
        description="What to return: notes, observations, relations, or a mix. Default: notes.",
    ),
]

# The API calls whole notes "entities"; "note" is the user-facing word.
_SEARCH_ITEM_TYPES: dict[ItemKind, SearchItemType] = {
    "note": SearchItemType.ENTITY,
    "observation": SearchItemType.OBSERVATION,
    "relation": SearchItemType.RELATION,
}


def search_item_types(items: list[ItemKind] | None) -> list[SearchItemType] | None:
    """Translate the user-facing item kinds into the API's search item types."""
    if not items:
        return None
    return [_SEARCH_ITEM_TYPES[item] for item in items]
