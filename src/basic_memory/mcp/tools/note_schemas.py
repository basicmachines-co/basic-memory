"""bm_schema: validate notes against their schemas, infer schemas, find drift.

Each command calls the schema API through SchemaClient and renders the report as
markdown, or returns it as JSON. "Nothing to report" is a NOT_FOUND refusal.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

from basic_memory.mcp.clients.schema import SchemaClient
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.refusals import api_refusals, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    OutputFormat,
    ProjectParam,
    ProjectSelector,
    parse_project,
    routing_arguments,
)
from basic_memory.schemas.schema import DriftReport, InferenceReport, ValidationReport

type SchemaCommand = Literal["validate", "infer", "diff"]


# --- Rendering ---


def _format_validation_report(report: ValidationReport) -> str:
    """Render a ValidationReport as readable markdown.

    Produces output the LLM can display directly instead of trying to
    interpret raw JSON, which leads to "undefined — invalid" rendering.
    """
    lines: list[str] = []

    # --- Header ---
    type_label = report.note_type or "all"
    lines.append(f"# Schema Validation: {type_label}")
    lines.append("")
    lines.append(
        f"Notes: {report.total_notes} | Valid: {report.valid_count} "
        f"| Warnings: {report.warning_count} | Errors: {report.error_count}"
    )
    lines.append("")

    # --- Per-type breakdown (all-types mode) ---
    if report.type_summaries:
        lines.append("## By Type")
        lines.append("")
        for summary in report.type_summaries:
            if summary.total_entities == 0:
                lines.append(f"- **{summary.note_type}**: no notes")
            else:
                lines.append(
                    f"- **{summary.note_type}**: {summary.valid_count}/{summary.total_notes} valid"
                )
        lines.append("")

    # --- Per-note results ---
    for r in report.results:
        status = "valid" if r.passed else "INVALID"
        lines.append(f"- **{r.note_identifier}** — {status}")
        for w in r.warnings:
            lines.append(f"  - warning: {w}")
        for e in r.errors:
            lines.append(f"  - error: {e}")

    return "\n".join(lines)


def _format_inference_report(report: InferenceReport) -> str:
    """Render an InferenceReport as readable markdown.

    Without this formatter the LLM receives raw JSON and renders
    field names as "undefined".
    """
    lines: list[str] = []

    # --- Header ---
    lines.append(f"# Schema Inference: {report.note_type}")
    lines.append("")
    lines.append(f"Notes analyzed: {report.notes_analyzed}")
    lines.append("")

    # --- Suggested schema YAML ---
    if report.suggested_schema:
        lines.append("## Suggested Schema")
        lines.append("")
        lines.append("```yaml")
        lines.append("---")
        lines.append(f"title: {report.note_type.title()}")
        lines.append("type: schema")
        lines.append(f"entity: {report.note_type}")
        lines.append("version: 1")
        lines.append("schema:")
        for field_name, field_def in report.suggested_schema.items():
            lines.append(f"  {field_name}: {field_def}")
        lines.append("---")
        lines.append("```")
        lines.append("")

    # --- Field frequency table ---
    if report.field_frequencies:
        lines.append("## Field Frequencies")
        lines.append("")
        for f in report.field_frequencies:
            pct = f"{f.percentage:.0%}"
            req_marker = "required" if f.name in report.suggested_required else "optional"
            samples = ", ".join(f.sample_values[:3]) if f.sample_values else ""
            sample_str = f" (e.g. {samples})" if samples else ""
            lines.append(
                f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total}) "
                f"[{req_marker}]{sample_str}"
            )
        lines.append("")

    # --- Excluded fields ---
    if report.excluded:
        lines.append("## Excluded (below threshold)")
        lines.append("")
        for name in report.excluded:
            lines.append(f"- {name}")
        lines.append("")

    return "\n".join(lines)


def _format_drift_report(report: DriftReport) -> str:
    """Render a DriftReport as readable markdown.

    Without this formatter the LLM receives raw JSON and renders
    field names as "undefined".
    """
    lines: list[str] = []

    # --- Header ---
    lines.append(f"# Schema Drift: {report.note_type}")
    lines.append("")

    has_drift = report.new_fields or report.dropped_fields or report.cardinality_changes

    if not has_drift:
        lines.append("No drift detected — schema matches actual usage.")
        return "\n".join(lines)

    # --- New fields ---
    if report.new_fields:
        lines.append("## New Fields (in notes but not in schema)")
        lines.append("")
        for f in report.new_fields:
            pct = f"{f.percentage:.0%}"
            lines.append(f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total})")
        lines.append("")

    # --- Dropped fields ---
    if report.dropped_fields:
        lines.append("## Dropped Fields (in schema but rare in notes)")
        lines.append("")
        for f in report.dropped_fields:
            pct = f"{f.percentage:.0%}"
            lines.append(f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total})")
        lines.append("")

    # --- Cardinality changes ---
    if report.cardinality_changes:
        lines.append("## Cardinality Changes")
        lines.append("")
        for change in report.cardinality_changes:
            lines.append(f"- {change}")
        lines.append("")

    return "\n".join(lines)


def _nothing_to_report(output_format: OutputFormat, message: str, guidance: str) -> ToolError:
    """A NOT_FOUND refusal: the short message in JSON, the full guidance as text (C11).

    A success that describes a failure reads as success to a client that checks
    isError, so "nothing to report" is a refusal.
    """
    text = message if output_format == "json" else guidance
    return refusal("NOT_FOUND", text, output_format=output_format)


def _no_notes_guidance(note_type: str, command: SchemaCommand) -> str:
    """Build guidance string when no notes of a given type exist.

    Used by validate, infer and diff when the project has zero notes of the
    requested type — a different situation from "notes exist but no schema".
    """
    return (
        f"# No Notes Found of Type '{note_type}'\n\n"
        f"`bm_schema(command={command!r}, note_type={note_type!r})` found no notes "
        f"with type '{note_type}' in the project.\n\n"
        f"## Next Steps\n\n"
        f"1. **Create notes of this type** — use `bm_write` with "
        f'`note_type="{note_type}"` to create notes\n'
        f"2. **Check existing types** — use `bm_search` with `note_types` "
        f"filter to see what types exist\n"
        f"3. **Browse content** — use `bm_list` to see what's in the project\n"
    )


def _no_schemas_defined_guidance() -> str:
    """Build guidance string when validating all types but no schemas exist.

    Used by validate when called without arguments in a project that
    has no schema notes — there is nothing to validate yet.
    """
    return (
        "# No Schemas Defined\n\n"
        "`bm_schema(command='validate')` was called without `note_type` or `identifier`, which "
        "validates every note type that has a schema — but this project has "
        "no schema notes yet, so there is nothing to validate.\n\n"
        "## Next Steps\n\n"
        "1. **Infer a schema** — run `bm_schema(command='infer', note_type='<note_type>')` to analyze "
        "existing notes and get a suggested schema\n"
        "2. **Create a schema note** — write a note with `type: schema` and an "
        "`entity` field naming the note type it validates\n"
        "3. **Re-run** — call `bm_schema(command='validate')` again once a schema exists\n"
    )


def _no_schema_guidance(note_type: str, command: Literal["validate", "diff"]) -> str:
    """Build guidance string when no schema exists for a note type.

    Used by validate and diff to explain what happened
    and how to create a schema.
    """
    return (
        f"# No Schema Found for '{note_type}'\n\n"
        f"`bm_schema(command={command!r}, note_type={note_type!r})` requires a schema "
        f"note to exist for type '{note_type}'.\n\n"
        f"## How to Create a Schema\n\n"
        f"1. **Infer from existing notes** — run `bm_schema(command='infer', note_type={note_type!r})` to "
        f"analyze your notes and get a suggested schema\n"
        f"2. **Create a schema note** — write a markdown file with this frontmatter:\n\n"
        f"```yaml\n"
        f"---\n"
        f"title: {note_type.title()}\n"
        f"type: schema\n"
        f"entity: {note_type}\n"
        f"version: 1\n"
        f"schema:\n"
        f"  name: string, full name\n"
        f"  role?: string, job title\n"
        f"settings:\n"
        f"  validation: warn\n"
        f"---\n"
        f"```\n\n"
        f"Schema fields use Picoschema notation:\n"
        f"- `field_name: type, description` — required field\n"
        f"- `field_name?: type, description` — optional field\n"
        f"- Supported types: `string`, `number`, `boolean`, `string[]`\n\n"
        f"3. **Index** — run `bm reindex --search` or wait for the "
        f"file watcher to pick up the new schema note\n"
        f"4. **Re-run** — call `bm_schema(command={command!r}, note_type={note_type!r})` again\n"
    )


# --- Commands ---


@asynccontextmanager
async def _schema_client(
    project: ProjectSelector, context: Context | None
) -> AsyncIterator[SchemaClient]:
    """A SchemaClient routed to the project the caller named."""
    project_name, project_id = routing_arguments(project)
    async with get_project_client(project_name, context=context, project_id=project_id) as (
        client,
        active_project,
    ):
        yield SchemaClient(client, active_project.external_id)


async def _validate(
    schemas: SchemaClient,
    note_type: str | None,
    identifier: str | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """Validate one note, one note type, or every type that has a schema."""
    # The API refuses an invalid schema definition with a 400.
    with api_refusals(output_format):
        result = await schemas.validate(note_type=note_type, identifier=identifier)

    # --- All-types mode ---
    # Trigger: called with neither identifier nor note_type (#1013)
    # Why: an empty report here means "no schemas defined", not
    #   "no notes of type 'unknown'" — the guards below would mislead
    # Outcome: per-type summary, or guidance for defining a first schema
    if note_type is None and identifier is None:
        if not result.type_summaries:
            raise _nothing_to_report(
                output_format,
                "No schemas defined in this project",
                _no_schemas_defined_guidance(),
            )
        if output_format == "json":
            return result.model_dump(mode="json", exclude_none=True)
        return _format_validation_report(result)

    # --- No notes guard ---
    # Trigger: no entities of this type exist in the project
    # Why: can't validate notes that don't exist yet
    # Outcome: a NOT_FOUND refusal with guidance on creating notes of this type
    effective_type = note_type or result.note_type or "unknown"
    if result.total_entities == 0:
        raise _nothing_to_report(
            output_format,
            f"No notes found of type '{effective_type}'",
            _no_notes_guidance(effective_type, "validate"),
        )

    # --- No schema guard ---
    # Trigger: entities exist but none were validated (no schema found)
    # Why: notes of this type exist but no schema was found, so none were validated
    # Outcome: a NOT_FOUND refusal with guidance on how to create a schema
    if result.total_notes == 0:
        raise _nothing_to_report(
            output_format,
            f"No schema found for type '{effective_type}'",
            _no_schema_guidance(effective_type, "validate"),
        )

    if output_format == "json":
        return result.model_dump(mode="json", exclude_none=True)
    return _format_validation_report(result)


async def _infer(
    schemas: SchemaClient,
    note_type: str,
    threshold: float,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """Suggest a schema from the notes of one type."""
    with api_refusals(output_format):
        result = await schemas.infer(note_type, threshold=threshold)

    # --- No notes guard ---
    # Trigger: no notes of this type exist, so nothing was analyzed
    # Why: an empty report would read as a successful inference (C11)
    # Outcome: a NOT_FOUND refusal with guidance on creating notes of this type
    if result.notes_analyzed == 0:
        raise _nothing_to_report(
            output_format,
            f"No notes found of type '{note_type}'",
            _no_notes_guidance(note_type, "infer"),
        )

    # --- Empty schema guard ---
    # Trigger: notes were analyzed but no fields met the threshold
    # Why: returning hundreds of excluded fields overwhelms the LLM context
    # Outcome: a NOT_FOUND refusal with guidance instead of a massive empty result
    if not result.suggested_schema:
        raise _nothing_to_report(
            output_format,
            f"No schema pattern found for '{note_type}' (threshold: {threshold:.0%})",
            f"# No Schema Pattern Found\n\n"
            f"Analyzed {result.notes_analyzed} notes of type '{note_type}', "
            f"but no observation or relation appeared in enough notes to suggest "
            f"a schema (threshold: {threshold:.0%}).\n\n"
            f"This usually means '{note_type}' is too broad — the notes don't "
            f"share a consistent structure.\n\n"
            f"## Suggestions\n"
            f"1. **Use a more specific type** — try `bm_search` with "
            f"`note_types` filter to see what types exist\n"
            f"2. **Lower the threshold** — "
            f"`bm_schema(command='infer', note_type={note_type!r}, threshold=0.1)` to include "
            f"rarer fields\n"
            f"3. **Create typed notes** — use `bm_write` with a specific "
            f'`note_type` (e.g., "person", "meeting") to build consistent '
            f"structure\n",
        )

    if output_format == "json":
        return result.model_dump(mode="json", exclude_none=True)
    return _format_inference_report(result)


async def _diff(
    schemas: SchemaClient,
    note_type: str,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    """Compare a note type's schema with how its notes are actually written."""
    # The API refuses an invalid schema definition with a 400.
    with api_refusals(output_format):
        result = await schemas.diff(note_type)

    # --- No schema guard ---
    # Trigger: API reports no schema was found for this type
    # Why: diff requires a schema to compare against
    # Outcome: a NOT_FOUND refusal with guidance on how to create a schema
    if not result.schema_found:
        raise _nothing_to_report(
            output_format,
            f"No schema found for type '{note_type}'",
            _no_schema_guidance(note_type, "diff"),
        )

    # --- No notes guard ---
    # Trigger: a schema exists but no notes of the type do.
    # Why: an empty comparison reads as "no drift", a comparison that never ran.
    # Outcome: a NOT_FOUND refusal with guidance on creating notes of this type.
    if result.notes_compared == 0:
        raise _nothing_to_report(
            output_format,
            f"No notes found of type '{note_type}'",
            _no_notes_guidance(note_type, "diff"),
        )

    if output_format == "json":
        return result.model_dump(mode="json", exclude_none=True)
    return _format_drift_report(result)


# --- Tool ---


@mcp.tool(
    name="bm_schema",
    title="Note schemas",
    description=(
        "Basic Memory note schemas (Picoschema). `command`: validate notes against their "
        "schema (one `note_type`, one note by `identifier`, or every type that has a "
        "schema); infer a schema from the notes of a `note_type`; diff a `note_type`'s "
        "notes against its schema to find drift. A schema is a note with type: schema "
        "whose `entity` field names the note type it describes."
    ),
    tags={"schema"},
    annotations={
        "title": "Note schemas",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_schema(
    command: Annotated[SchemaCommand, Field(description="What to do.")],
    note_type: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("note_type", "type"),
            description="The frontmatter `type:` to check. Required for infer and diff.",
        ),
    ] = None,
    identifier: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices("identifier", "file_path"),
            description="For validate: one note, instead of a whole note type.",
        ),
    ] = None,
    threshold: Annotated[
        float,
        Field(
            ge=0,
            le=1,
            description=("For infer: the share of notes a field must appear in to be suggested."),
        ),
    ] = 0.25,
    output_format: OutputFormat = "text",
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> ValidationReport | str | dict[str, Any]:
    """Validate, infer or diff note schemas."""
    # A note's own type decides its schema, so naming both would validate one and
    # label the report as the other.
    if note_type is not None and identifier is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "Give `note_type` to validate a type, or `identifier` to validate one note.",
            output_format=output_format,
        )
    selector = parse_project(project)
    if command == "validate":
        async with _schema_client(selector, context) as schemas:
            return await _validate(schemas, note_type, identifier, output_format)
    if identifier is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"{command} works on a note type, not one note; give `note_type`.",
            output_format=output_format,
        )
    if note_type is None:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"{command} needs `note_type`, the frontmatter type to check.",
            try_instead="bm_find(filters={'type': ...}) to see which types exist",
            output_format=output_format,
        )
    async with _schema_client(selector, context) as schemas:
        if command == "infer":
            return await _infer(schemas, note_type, threshold, output_format)
        return await _diff(schemas, note_type, output_format)
