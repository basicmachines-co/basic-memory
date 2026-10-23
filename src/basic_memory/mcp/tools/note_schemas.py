"""bm_schema: validate notes against their schemas, infer schemas, find drift.

Each command runs the schema tool implementation of the same purpose; this tool
gives them one `command` parameter and the shared parameter types.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    OutputFormat,
    ProjectParam,
    parse_project,
    routing_arguments,
)
from basic_memory.mcp.tools.schema import schema_diff, schema_infer, schema_validate
from basic_memory.schemas.schema import ValidationReport

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
    project_name, project_id = routing_arguments(parse_project(project))
    if command == "validate":
        return await schema_validate(
            note_type=note_type,
            identifier=identifier,
            project=project_name,
            project_id=project_id,
            output_format=output_format,
            context=context,
        )
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
    if command == "infer":
        return await schema_infer(
            note_type=note_type,
            threshold=threshold,
            project=project_name,
            project_id=project_id,
            output_format=output_format,
            context=context,
        )
    return await schema_diff(
        note_type=note_type,
        project=project_name,
        project_id=project_id,
        output_format=output_format,
        context=context,
    )
