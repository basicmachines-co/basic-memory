"""bm_schema: validate notes against their schemas, infer schemas, find drift (#1832).

Replaces schema_validate, schema_infer and schema_diff (SPEC-35.2). It calls the
schema API through the typed SchemaClient, as they do, and shares their report
formatting; what differs is that "nothing to report" cases are refusals (C11), not
success payloads describing a failure.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

from basic_memory.mcp.clients.schema import SchemaClient
from basic_memory.mcp.note_targets import open_project_target
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.schema_reports import (
    format_drift_report,
    format_inference_report,
    format_validation_report,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam

type SchemaCommand = Literal["validate", "infer", "diff"]


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
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Validate, infer or diff note schemas."""
    if command != "validate" and identifier is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"{command} works on a note type, not one note; give `note_type`.",
            output_format=output_format,
        )

    # A note identifier routes like every bm_* target; otherwise only `project` does.
    async with open_project_target(
        identifier or "/", project, context, output_format=output_format
    ) as target:
        schema_client = SchemaClient(target.client, target.project.external_id)
        if command == "validate":
            note = target.route.path if identifier is not None else None
            return await _validate(schema_client, note_type, note, output_format)
        if note_type is None:
            raise refusal(
                "INVALID_ARGUMENTS",
                f"{command} needs `note_type`, the frontmatter type to check.",
                try_instead="bm_find(filters={'type': ...}) to see which types exist",
                output_format=output_format,
            )
        if command == "infer":
            return await _infer(schema_client, note_type, threshold, output_format)
        return await _diff(schema_client, note_type, output_format)


def _no_schema(note_type: str, output_format: OutputFormat) -> ToolError:
    return refusal(
        "NOT_FOUND",
        f"No schema note describes type '{note_type}'.",
        try_instead=(
            f"bm_schema(command='infer', note_type='{note_type}'), then bm_write the "
            "suggestion as a note with type: schema"
        ),
        output_format=output_format,
    )


async def _validate(
    schema_client: SchemaClient,
    note_type: str | None,
    identifier: str | None,
    output_format: OutputFormat,
) -> str | dict[str, Any]:
    report = await schema_client.validate(note_type=note_type, identifier=identifier)
    # Trigger: neither note_type nor identifier (every type that has a schema, #1013).
    # Why: an empty report then means no schemas exist, not a missing note type.
    if note_type is None and identifier is None:
        if not report.type_summaries:
            raise refusal(
                "NOT_FOUND",
                "This project has no schema notes, so there is nothing to validate.",
                try_instead="bm_schema(command='infer', note_type=...) to draft one",
                output_format=output_format,
            )
    else:
        checked_type = note_type or report.note_type or identifier or ""
        if report.total_entities == 0:
            raise refusal(
                "NOT_FOUND",
                f"No notes of type '{checked_type}' in this project.",
                try_instead="bm_find(filters={'type': ...}) to see which types exist",
                output_format=output_format,
            )
        # Notes exist, but none were validated: no schema applies to them.
        if report.total_notes == 0:
            raise _no_schema(checked_type, output_format)
    if output_format == "json":
        return report.model_dump(mode="json", exclude_none=True)
    return format_validation_report(report)


async def _infer(
    schema_client: SchemaClient, note_type: str, threshold: float, output_format: OutputFormat
) -> str | dict[str, Any]:
    report = await schema_client.infer(note_type, threshold=threshold)
    if report.notes_analyzed == 0:
        raise refusal(
            "NOT_FOUND",
            f"No notes of type '{note_type}' in this project.",
            try_instead="bm_find(filters={'type': ...}) to see which types exist",
            output_format=output_format,
        )
    # Every field fell below the threshold: the notes share no structure to suggest.
    if not report.suggested_schema:
        raise refusal(
            "NOT_FOUND",
            f"No field appears in {threshold:.0%} of the {report.notes_analyzed} "
            f"'{note_type}' notes, so there is no schema to suggest.",
            try_instead=f"bm_schema(command='infer', note_type='{note_type}', threshold=0.1)",
            output_format=output_format,
        )
    if output_format == "json":
        return report.model_dump(mode="json", exclude_none=True)
    return format_inference_report(report)


async def _diff(
    schema_client: SchemaClient, note_type: str, output_format: OutputFormat
) -> str | dict[str, Any]:
    report = await schema_client.diff(note_type)
    if not report.schema_found:
        raise _no_schema(note_type, output_format)
    if output_format == "json":
        return report.model_dump(mode="json", exclude_none=True)
    return format_drift_report(report)
