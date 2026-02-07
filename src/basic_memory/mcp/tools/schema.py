"""Schema tools for Basic Memory MCP server.

Provides tools for schema validation, inference, and drift detection through the MCP protocol.
These tools call the schema API endpoints via the typed SchemaClient.
"""

from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project
from basic_memory.mcp.server import mcp
from basic_memory.schemas.schema import ValidationReport, InferenceReport, DriftReport


@mcp.tool(
    description="Validate notes against their Picoschema definitions.",
)
async def schema_validate(
    entity_type: Optional[str] = None,
    identifier: Optional[str] = None,
    project: Optional[str] = None,
    context: Context | None = None,
) -> ValidationReport | str:
    """Validate notes against their resolved schema.

    Validates a specific note (by identifier) or all notes of a given type.
    Returns warnings/errors based on the schema's validation mode.

    Schemas are resolved in priority order:
    1. Inline schema (dict in frontmatter)
    2. Explicit reference (string in frontmatter)
    3. Implicit by type (type field matches schema note entity field)
    4. No schema (no validation)

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default.
    If project unknown, use list_memory_projects() first.

    Args:
        entity_type: Entity type to batch-validate (e.g., "Person").
            If provided, validates all notes of this type.
        identifier: Specific note to validate (permalink, title, or path).
            If provided, validates only this note.
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        ValidationReport with per-note results, or error guidance string

    Examples:
        # Validate all Person notes
        schema_validate(entity_type="Person")

        # Validate a specific note
        schema_validate(identifier="people/paul-graham")

        # Validate in a specific project
        schema_validate(entity_type="Person", project="my-research")
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=schema_validate project={active_project.name} "
            f"entity_type={entity_type} identifier={identifier}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.validate(
                entity_type=entity_type,
                identifier=identifier,
            )

            logger.info(
                f"MCP tool response: tool=schema_validate project={active_project.name} "
                f"total={result.total_notes} valid={result.valid_count} "
                f"warnings={result.warning_count} errors={result.error_count}"
            )
            return result

        except Exception as e:
            logger.error(
                f"Schema validation failed: {e}, project: {active_project.name}"
            )
            return (
                f"# Schema Validation Failed\n\n"
                f"Error validating schemas: {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure schema notes exist (type: schema) for the target entity type\n"
                f"2. Check that notes have the correct type in frontmatter\n"
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )


@mcp.tool(
    description="Analyze existing notes and suggest a Picoschema definition.",
)
async def schema_infer(
    entity_type: str,
    threshold: float = 0.25,
    project: Optional[str] = None,
    context: Context | None = None,
) -> InferenceReport | str:
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
        entity_type: The entity type to analyze (e.g., "Person", "meeting").
        threshold: Minimum frequency (0-1) for a field to be suggested as optional.
            Default 0.25 (25%). Fields above 95% become required.
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        InferenceReport with frequency data and suggested schema, or error string

    Examples:
        # Infer schema for Person notes
        schema_infer("Person")

        # Use a higher threshold (50% minimum)
        schema_infer("meeting", threshold=0.5)

        # Infer in a specific project
        schema_infer("Person", project="my-research")
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=schema_infer project={active_project.name} "
            f"entity_type={entity_type} threshold={threshold}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.infer(entity_type, threshold=threshold)

            logger.info(
                f"MCP tool response: tool=schema_infer project={active_project.name} "
                f"entity_type={entity_type} notes_analyzed={result.notes_analyzed} "
                f"required={len(result.suggested_required)} "
                f"optional={len(result.suggested_optional)}"
            )
            return result

        except Exception as e:
            logger.error(
                f"Schema inference failed: {e}, project: {active_project.name}"
            )
            return (
                f"# Schema Inference Failed\n\n"
                f"Error inferring schema for '{entity_type}': {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure notes of type '{entity_type}' exist in the project\n"
                f"2. Try searching: `search_notes(\"{entity_type}\", types=[\"{entity_type}\"])`\n"
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )


@mcp.tool(
    description="Detect drift between a schema definition and actual note usage.",
)
async def schema_diff(
    entity_type: str,
    project: Optional[str] = None,
    context: Context | None = None,
) -> DriftReport | str:
    """Detect drift between a schema definition and actual note usage.

    Compares the existing schema for an entity type against how notes of
    that type are actually structured. Identifies new fields that have
    appeared, declared fields that are rarely used, and cardinality changes
    (single-value vs array).

    Useful for evolving schemas as your knowledge base grows -- run
    periodically to see if your schema still matches reality.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default.
    If project unknown, use list_memory_projects() first.

    Args:
        entity_type: The entity type to check for drift (e.g., "Person").
        project: Project name. Optional -- server will resolve.
        context: Optional FastMCP context for performance caching.

    Returns:
        DriftReport with new fields, dropped fields, and cardinality changes,
        or error guidance string

    Examples:
        # Check drift for Person schema
        schema_diff("Person")

        # Check drift in a specific project
        schema_diff("Person", project="my-research")
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=schema_diff project={active_project.name} "
            f"entity_type={entity_type}"
        )

        try:
            from basic_memory.mcp.clients.schema import SchemaClient

            schema_client = SchemaClient(client, active_project.external_id)
            result = await schema_client.diff(entity_type)

            logger.info(
                f"MCP tool response: tool=schema_diff project={active_project.name} "
                f"entity_type={entity_type} "
                f"new_fields={len(result.new_fields)} "
                f"dropped_fields={len(result.dropped_fields)} "
                f"cardinality_changes={len(result.cardinality_changes)}"
            )
            return result

        except Exception as e:
            logger.error(
                f"Schema diff failed: {e}, project: {active_project.name}"
            )
            return (
                f"# Schema Diff Failed\n\n"
                f"Error detecting drift for '{entity_type}': {e}\n\n"
                f"## Troubleshooting\n"
                f"1. Ensure a schema note exists for entity type '{entity_type}'\n"
                f"2. Ensure notes of type '{entity_type}' exist in the project\n"
                f"3. Verify the project has been synced: `basic-memory status`\n"
            )
