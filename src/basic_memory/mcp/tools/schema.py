"""Schema tools for Basic Memory MCP server.

Provides tools for schema validation, inference, and drift detection through the MCP protocol.
These tools call the schema API endpoints via the typed SchemaClient.
"""

from typing import Literal, Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import mcp
from basic_memory.schemas.schema import ValidationReport, InferenceReport, DriftReport


def _no_notes_guidance(note_type: str, tool_name: str) -> str:
    """Build guidance string when no notes of a given type exist.

    Used by schema_validate when the project has zero notes of the
    requested type — a different situation from "notes exist but no schema".
    """
    return (
        f"# No Notes Found of Type '{note_type}'\n\n"
        f"`{tool_name}` found no notes with type '{note_type}' in the project.\n\n"
        f"## Next Steps\n\n"
        f"1. **Create notes of this type** — use `write_note` with "
        f'`note_type="{note_type}"` to create notes\n'
        f"2. **Check existing types** — use `search_notes` with `entity_types` "
        f"filter to see what types exist\n"
        f"3. **Browse content** — use `list_directory` or `recent_activity` to "
        f"see what's in the project\n"
    )


def _no_schema_guidance(note_type: str, tool_name: str) -> str:
    """Build guidance string when no schema exists for a note type.

    Used by schema_validate and schema_diff to explain what happened
    and how to create a schema.
    """
    return (
        f"# No Schema Found for '{note_type}'\n\n"
        f"`{tool_name}` requires a schema note to exist for type '{note_type}'.\n\n"
        f"## How to Create a Schema\n\n"
        f'1. **Infer from existing notes** — run `schema_infer("{note_type}")` to '
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
        f"3. **Sync** — run `basic-memory sync` or wait for auto-sync to pick up "
        f"the new schema note\n"
        f'4. **Re-run** — call `{tool_name}("{note_type}")` again\n'
    )


@mcp.tool(
    description="Validate notes against their Picoschema definitions.",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def schema_validate(
    note_type: Optional[str] = None,
    identifier: Optional[str] = None,
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> ValidationReport | str | dict:
    """Validate notes against their resolved schema.

    Validates a specific note (by identifier) or all notes of a given type.
    Returns warnings/errors based on the schema's validation mode.

    Schemas are resolved in priority order:
    1. Inline schema (dict in frontmatter)
    2. Explicit reference (string in frontmatter)
    3. Implicit by type (type field matches schema note's entity field)
    4. No schema (no validation)

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default.
    If project unknown, use list_memory_projects() first.

    Args:
        note_type: Note type to batch-validate (e.g., "person", "meeting").
            If provided, validates all notes of this type.
        identifier: Specific note to validate (permalink, title, or path).
            If provided, validates only this note.
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        ValidationReport with per-note results, or error guidance string

    Examples:
        # Validate all person notes
        schema_validate(note_type="person")

        # Validate a specific note
        schema_validate(identifier="people/paul-graham")

        # Validate in a specific project
        schema_validate(note_type="person", project="my-research")
    """
    async with get_project_client(project, workspace, context) as (client, active_project):
        logger.info(
            f"MCP tool call tool=schema_validate project={active_project.name} "
            f"note_type={note_type} identifier={identifier}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.validate(
                note_type=note_type,
                identifier=identifier,
            )

            logger.info(
                f"MCP tool response: tool=schema_validate project={active_project.name} "
                f"total={result.total_notes} valid={result.valid_count} "
                f"warnings={result.warning_count} errors={result.error_count}"
            )

            # --- No notes guard ---
            # Trigger: no entities of this type exist in the project
            # Why: can't validate notes that don't exist yet
            # Outcome: return guidance on creating notes of this type
            if note_type and result.total_entities == 0:
                if output_format == "json":
                    return {"error": f"No notes found of type '{note_type}'"}
                return _no_notes_guidance(note_type, "schema_validate")

            # --- No schema guard ---
            # Trigger: entities exist but none were validated (no schema found)
            # Why: notes of this type exist but no schema was found, so none were validated
            # Outcome: return guidance on how to create a schema
            if note_type and result.total_notes == 0:
                if output_format == "json":
                    return {"error": f"No schema found for type '{note_type}'"}
                return _no_schema_guidance(note_type, "schema_validate")

            if output_format == "json":
                return result.model_dump(mode="json", exclude_none=True)

            return result

        except Exception as e:
            logger.error(f"Schema validation failed: {e}, project: {active_project.name}")
            if output_format == "json":
                return {"error": f"Schema validation failed: {e}"}
            return (
                f"# Schema Validation Failed\n\n"
                f"Error validating schemas: {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure schema notes exist (type: schema) for the target note type\n"
                f"2. Check that notes have the correct type in frontmatter\n"
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )


@mcp.tool(
    description="Analyze existing notes and suggest a Picoschema definition.",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def schema_infer(
    note_type: str,
    threshold: float = 0.25,
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> InferenceReport | str | dict:
    """Analyze existing notes and suggest a schema definition.

    Examines observation categories and relation types across all notes
    of the given type. Returns frequency analysis and suggested Picoschema
    YAML that can be saved as a schema note.

    Frequency thresholds:
    - 95%+ present -> required field
    - threshold+ present -> optional field
    - Below threshold -> excluded (but noted)

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default.
    If project unknown, use list_memory_projects() first.

    Args:
        note_type: The note type to analyze (e.g., "person", "meeting").
        threshold: Minimum frequency (0-1) for a field to be suggested as optional.
            Default 0.25 (25%). Fields above 95% become required.
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        InferenceReport with frequency data and suggested schema, or error string

    Examples:
        # Infer schema for person notes
        schema_infer("person")

        # Use a higher threshold (50% minimum)
        schema_infer("meeting", threshold=0.5)

        # Infer in a specific project
        schema_infer("person", project="my-research")
    """
    async with get_project_client(project, workspace, context) as (client, active_project):
        logger.info(
            f"MCP tool call tool=schema_infer project={active_project.name} "
            f"note_type={note_type} threshold={threshold}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.infer(note_type, threshold=threshold)

            logger.info(
                f"MCP tool response: tool=schema_infer project={active_project.name} "
                f"note_type={note_type} notes_analyzed={result.notes_analyzed} "
                f"required={len(result.suggested_required)} "
                f"optional={len(result.suggested_optional)}"
            )

            # --- Empty schema guard ---
            # Trigger: notes were analyzed but no fields met the threshold
            # Why: returning hundreds of excluded fields overwhelms the LLM context
            # Outcome: return actionable guidance instead of a massive empty result
            if result.notes_analyzed > 0 and not result.suggested_schema:
                if output_format == "json":
                    return {
                        "error": (
                            f"No schema pattern found for '{note_type}' "
                            f"(threshold: {threshold:.0%})"
                        )
                    }
                return (
                    f"# No Schema Pattern Found\n\n"
                    f"Analyzed {result.notes_analyzed} notes of type '{note_type}', "
                    f"but no observation or relation appeared in enough notes to suggest "
                    f"a schema (threshold: {threshold:.0%}).\n\n"
                    f"This usually means '{note_type}' is too broad — the notes don't "
                    f"share a consistent structure.\n\n"
                    f"## Suggestions\n"
                    f"1. **Use a more specific type** — try `search_notes` with "
                    f"`entity_types` filter to see what types exist\n"
                    f"2. **Lower the threshold** — "
                    f'`schema_infer("{note_type}", threshold=0.1)` to include '
                    f"rarer fields\n"
                    f"3. **Create typed notes** — use `write_note` with a specific "
                    f'`note_type` (e.g., "person", "meeting") to build consistent '
                    f"structure\n"
                )

            if output_format == "json":
                return result.model_dump(mode="json", exclude_none=True)

            return result

        except Exception as e:
            logger.error(f"Schema inference failed: {e}, project: {active_project.name}")
            if output_format == "json":
                return {"error": f"Schema inference failed: {e}"}
            return (
                f"# Schema Inference Failed\n\n"
                f"Error inferring schema for type '{note_type}': {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure notes of type '{note_type}' exist in the project\n"
                f'2. Try searching: `search_notes("{note_type}", note_types=["{note_type}"])`\n'
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )


@mcp.tool(
    description="Detect drift between a schema definition and actual note usage.",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def schema_diff(
    note_type: str,
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> DriftReport | str | dict:
    """Detect drift between a schema definition and actual note usage.

    Compares the existing schema for a note type against how notes of
    that type are actually structured. Identifies new fields that have
    appeared, declared fields that are rarely used, and cardinality changes
    (single-value vs array).

    Useful for evolving schemas as your knowledge base grows -- run
    periodically to see if your schema still matches reality.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default.
    If project unknown, use list_memory_projects() first.

    Args:
        note_type: The note type to check for drift (e.g., "person").
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        DriftReport with new fields, dropped fields, and cardinality changes,
        or error guidance string

    Examples:
        # Check drift for person schema
        schema_diff("person")

        # Check drift in a specific project
        schema_diff("person", project="my-research")
    """
    async with get_project_client(project, workspace, context) as (client, active_project):
        logger.info(
            f"MCP tool call tool=schema_diff project={active_project.name} note_type={note_type}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.diff(note_type)

            logger.info(
                f"MCP tool response: tool=schema_diff project={active_project.name} "
                f"note_type={note_type} schema_found={result.schema_found} "
                f"new_fields={len(result.new_fields)} "
                f"dropped_fields={len(result.dropped_fields)} "
                f"cardinality_changes={len(result.cardinality_changes)}"
            )

            # --- No schema guard ---
            # Trigger: API reports no schema was found for this type
            # Why: diff requires a schema to compare against
            # Outcome: return guidance on how to create a schema
            if not result.schema_found:
                if output_format == "json":
                    return {"error": f"No schema found for type '{note_type}'"}
                return _no_schema_guidance(note_type, "schema_diff")

            if output_format == "json":
                return result.model_dump(mode="json", exclude_none=True)

            return result

        except Exception as e:
            logger.error(f"Schema diff failed: {e}, project: {active_project.name}")
            if output_format == "json":
                return {"error": f"Schema diff failed: {e}"}
            return (
                f"# Schema Diff Failed\n\n"
                f"Error detecting drift for type '{note_type}': {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure a schema note exists for type '{note_type}'\n"
                f"2. Ensure notes of type '{note_type}' exist in the project\n"
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )
