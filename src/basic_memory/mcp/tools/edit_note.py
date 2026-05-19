"""Edit note tool for Basic Memory MCP server."""

from typing import Annotated, Optional, Literal

import logfire
from loguru import logger
from fastmcp import Context
from pydantic import AliasChoices, Field

from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    _workspace_identifier_discovery_available,
    detect_project_from_memory_url_prefix,
    get_project_client,
    add_project_metadata,
    resolve_project_and_path,
)
from basic_memory.mcp.server import mcp
from basic_memory.schemas.base import Entity
from basic_memory.schemas.response import EntityResponse
from basic_memory.services.link_resolver import (
    detect_project_from_workspace_identifier_prefix,
    is_workspace_qualified_plain_identifier,
)
from basic_memory.utils import normalize_project_reference, validate_project_path


def _parse_identifier_to_title_and_directory(identifier: str) -> tuple[str, str]:
    """Parse an identifier into (title, directory) for creating a new note.

    Strips memory:// prefix if present, then splits on the last '/' to
    separate the directory path from the note title.

    Examples:
        "conversations/my-note" → ("my-note", "conversations")
        "my-note"              → ("my-note", "")
        "a/b/c/my-note"       → ("my-note", "a/b/c")
        "memory://a/b/note"   → ("note", "a/b")
    """
    cleaned = identifier
    if cleaned.startswith("memory://"):
        cleaned = cleaned[len("memory://") :]

    if "/" in cleaned:
        last_slash = cleaned.rfind("/")
        directory = cleaned[:last_slash]
        title = cleaned[last_slash + 1 :]
    else:
        directory = ""
        title = cleaned

    return title, directory


def _compose_workspace_project_route(
    *,
    workspace: Optional[str],
    project: Optional[str],
    project_id: Optional[str],
) -> Optional[str]:
    """Return the explicit project route requested by workspace/project args."""
    if workspace is None:
        return project

    cleaned_workspace = workspace.strip().strip("/")
    if not cleaned_workspace:
        raise ValueError("workspace must not be empty when provided")
    if "/" in cleaned_workspace:
        raise ValueError("workspace must be a single workspace slug, name, or tenant_id")
    if project_id is not None:
        raise ValueError("workspace cannot be combined with project_id; use project_id alone")
    if project is None or not project.strip().strip("/"):
        raise ValueError("workspace requires an explicit project argument")

    cleaned_project = project.strip().strip("/")
    if "/" in cleaned_project:
        raise ValueError(
            "Use either workspace='workspace' with project='project', "
            "or project='workspace/project', not both"
        )
    return f"{cleaned_workspace}/{cleaned_project}"


def _format_ambiguous_workspace_identifier_response(
    *,
    identifier: str,
    detected_project: str,
) -> str:
    """Format the safe-stop response for ambiguous plain write identifiers."""
    cleaned_identifier = identifier.strip()
    normalized_identifier = normalize_project_reference(cleaned_identifier).strip("/")
    workspace_hint, project_hint, note_identifier = normalized_identifier.split("/", 2)

    return f"""# Edit Failed - Ambiguous Identifier

`{cleaned_identifier}` could refer to a local note path in the active project, or to a note in `{detected_project}`.

Because edit_note changes content, Basic Memory will not infer a workspace route from a plain path.

Retry with one of these explicit routes:
- `edit_note(identifier="{note_identifier}", project="{detected_project}", operation=..., content=...)`
- `edit_note(identifier="{note_identifier}", workspace="{workspace_hint}", project="{project_hint}", operation=..., content=...)`
- `edit_note(identifier="memory://{normalized_identifier}", operation=..., content=...)`
- `edit_note(identifier="{note_identifier}", project_id="<project external_id>", operation=..., content=...)`"""


def _format_error_response(
    error_message: str,
    operation: str,
    identifier: str,
    find_text: Optional[str] = None,
    expected_replacements: int = 1,
    project: Optional[str] = None,
) -> str:
    """Format helpful error responses for edit_note failures that guide the AI to retry successfully."""

    # Entity not found errors — only reachable for find_replace/replace_section
    # because append/prepend auto-create the note when it doesn't exist
    if "Entity not found" in error_message or "entity not found" in error_message.lower():
        return f"""# Edit Failed - Note Not Found

The note with identifier '{identifier}' could not be found. The `find_replace` and `replace_section` operations require an existing note with content to modify.

**Tip:** `append` and `prepend` operations automatically create the note if it doesn't exist.

## Suggestions to try:
1. **Use append/prepend instead**: These operations will create the note automatically if it doesn't exist
2. **Search for the note first**: Use `search_notes("{project or "project-name"}", "{identifier.split("/")[-1]}")` to find similar notes with exact identifiers
3. **Try different exact identifier formats**:
   - If you used a permalink like "folder/note-title", try the exact title: "{identifier.split("/")[-1].replace("-", " ").title()}"
   - If you used a title, try the exact permalink format: "{identifier.lower().replace(" ", "-")}"
   - Use `read_note("{project or "project-name"}", "{identifier}")` first to verify the note exists and get the exact identifier

## Alternative approach:
Use `write_note("{project or "project-name"}", "title", "content", "folder")` to create the note first, then edit it."""

    # Find/replace specific errors
    if operation == "find_replace":
        if "Text to replace not found" in error_message:
            return f"""# Edit Failed - Text Not Found

The text '{find_text}' was not found in the note '{identifier}'.

## Suggestions to try:
1. **Read the note first**: Use `read_note("{project or "project-name"}", "{identifier}")` to see the current content
2. **Check for exact matches**: The search is case-sensitive and must match exactly
3. **Try a broader search**: Search for just part of the text you want to replace
4. **Use expected_replacements=0**: If you want to verify the text doesn't exist

## Alternative approaches:
- Use `append` or `prepend` to add new content instead
- Use `replace_section` if you're trying to update a specific section"""

        if "Expected" in error_message and "occurrences" in error_message:
            # Extract the actual count from error message if possible
            import re

            match = re.search(r"found (\d+)", error_message)
            actual_count = match.group(1) if match else "a different number of"

            return f"""# Edit Failed - Wrong Replacement Count

Expected {expected_replacements} occurrences of '{find_text}' but found {actual_count}.

## How to fix:
1. **Read the note first**: Use `read_note("{project or "project-name"}", "{identifier}")` to see how many times '{find_text}' appears
2. **Update expected_replacements**: Set expected_replacements={actual_count} in your edit_note call
3. **Be more specific**: If you only want to replace some occurrences, make your find_text more specific

## Example:
```
edit_note("{project or "project-name"}", "{identifier}", "find_replace", "new_text", find_text="{find_text}", expected_replacements={actual_count})
```"""

    # Section replacement errors
    if operation == "replace_section" and "Multiple sections" in error_message:
        return f"""# Edit Failed - Duplicate Section Headers

Multiple sections found with the same header in note '{identifier}'.

## How to fix:
1. **Read the note first**: Use `read_note("{project or "project-name"}", "{identifier}")` to see the document structure
2. **Make headers unique**: Add more specific text to distinguish sections
3. **Use append instead**: Add content at the end rather than replacing a specific section

## Alternative approach:
Use `find_replace` to update specific text within the duplicate sections."""

    # Generic server/request errors
    if (
        "Invalid request" in error_message or "malformed" in error_message.lower()
    ):  # pragma: no cover
        return f"""# Edit Failed - Request Error

There was a problem with the edit request to note '{identifier}': {error_message}.

## Common causes and fixes:
1. **Note doesn't exist**: Use `search_notes("{project or "project-name"}", "query")` or `read_note("{project or "project-name"}", "{identifier}")` to verify the note exists
2. **Invalid identifier format**: Try different identifier formats (title vs permalink)
3. **Empty or invalid content**: Check that your content is properly formatted
4. **Server error**: Try the operation again, or use `read_note()` first to verify the note state

## Troubleshooting steps:
1. Verify the note exists: `read_note("{project or "project-name"}", "{identifier}")`
2. If not found, search for it: `search_notes("{project or "project-name"}", "{identifier.split("/")[-1]}")`
3. Try again with the correct identifier from the search results"""

    # Fallback for other errors
    return f"""# Edit Failed

Error editing note '{identifier}': {error_message}

## General troubleshooting:
1. **Verify the note exists**: Use `read_note("{project or "project-name"}", "{identifier}")` to check
2. **Check your parameters**: Ensure all required parameters are provided correctly
3. **Read the note content first**: Use `read_note("{project or "project-name"}", "{identifier}")` to understand the current structure
4. **Try a simpler operation**: Start with `append` if other operations fail

## Need help?
- Use `search_notes("{project or "project-name"}", "query")` to find notes
- Use `read_note("{project or "project-name"}", "identifier")` to examine content before editing
- Check that identifiers, section headers, and find_text match exactly"""


@mcp.tool(
    description="Edit an existing markdown note using various operations like append, prepend, find_replace, replace_section, insert_before_section, or insert_after_section.",
    annotations={"destructiveHint": False, "openWorldHint": False},
)
async def edit_note(
    identifier: str,
    operation: str,
    # Accept common replacement-content aliases. Models trained on diff/patch
    # APIs reach for new_content/replacement/replace_with on first try.
    content: Annotated[
        str,
        Field(
            validation_alias=AliasChoices("content", "new_content", "replacement", "replace_with")
        ),
    ],
    project: Optional[str] = None,
    workspace: Optional[str] = None,
    project_id: Optional[str] = None,
    # Section/heading naming varies across tools; accept the descriptive forms.
    section: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("section", "section_heading", "heading"),
        ),
    ] = None,
    # find_text is the highest-frequency miss per the issue: models reach for
    # find/old_text/old_content/search before find_text every time.
    find_text: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("find_text", "find", "old_text", "old_content", "search"),
        ),
    ] = None,
    expected_replacements: Optional[int] = None,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """Edit an existing markdown note in the knowledge base.

    Makes targeted changes to existing notes without rewriting the entire content.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        identifier: The exact title, permalink, or memory:// URL of the note to edit.
                   Must be an exact match - fuzzy matching is not supported for edit operations.
                   Use search_notes() or read_note() first to find the correct identifier if uncertain.
        operation: The editing operation to perform:
                  - "append": Add content to the end of the note (creates the note if it doesn't exist)
                  - "prepend": Add content to the beginning of the note (creates the note if it doesn't exist)
                  - "find_replace": Replace occurrences of find_text with content (note must exist)
                  - "replace_section": Replace content under a specific markdown header (note must exist)
                  - "insert_before_section": Insert content before a section heading without consuming it (note must exist)
                  - "insert_after_section": Insert content after a section heading without consuming it (note must exist)
        content: The content to add or use for replacement
        project: Project name to edit in. Optional - server will resolve using hierarchy.
                Use "workspace/project" to route to a project in a specific cloud workspace.
                If unknown, use list_memory_projects() to discover available projects.
        workspace: Workspace slug, name, or tenant_id. When provided with `project`,
                routes as `workspace/project`. Cannot be combined with `project_id`.
        project_id: Project external_id (UUID). Prefer this over `project` when known —
                it routes to the exact project regardless of name collisions across cloud
                workspaces. Takes precedence over `project`. Get from list_memory_projects().
        section: For replace_section operation - the markdown header to replace content under (e.g., "## Notes", "### Implementation")
        find_text: For find_replace operation - the text to find and replace
        expected_replacements: For find_replace operation - the expected number of replacements (validation will fail if actual doesn't match)
        output_format: "text" returns the existing markdown summary. "json" returns
            machine-readable edit metadata.
        context: Optional FastMCP context for performance caching.

    Returns:
        A markdown formatted summary of the edit operation and resulting semantic content,
        including operation details, file path, observations, relations, and project metadata.

    Examples:
        # Add new content to end of note
        edit_note("my-project", "project-planning", "append", "\\n## New Requirements\\n- Feature X\\n- Feature Y")

        # Add timestamp at beginning (frontmatter-aware)
        edit_note("work-docs", "meeting-notes", "prepend", "## 2025-05-25 Update\\n- Progress update...\\n\\n")

        # Update version number (single occurrence)
        edit_note("api-project", "config-spec", "find_replace", "v0.13.0", find_text="v0.12.0")

        # Update version in multiple places with validation
        edit_note("docs-project", "api-docs", "find_replace", "v2.1.0", find_text="v2.0.0", expected_replacements=3)

        # Replace text that appears multiple times - validate count first
        edit_note("team-docs", "docs/guide", "find_replace", "new-api", find_text="old-api", expected_replacements=5)

        # Replace implementation section
        edit_note("specs", "api-spec", "replace_section", "New implementation approach...\\n", section="## Implementation")

        # Replace subsection with more specific header
        edit_note("docs", "docs/setup", "replace_section", "Updated install steps\\n", section="### Installation")

        # Using different identifier formats (must be exact matches)
        edit_note("work-project", "Meeting Notes", "append", "\\n- Follow up on action items")  # exact title
        edit_note("work-project", "docs/meeting-notes", "append", "\\n- Follow up tasks")       # exact permalink

        # If uncertain about identifier, search first:
        # search_notes("work-project", "meeting")  # Find available notes
        # edit_note("work-project", "docs/meeting-notes-2025", "append", "content")  # Use exact result

        # Add new section to document
        edit_note("planning", "project-plan", "replace_section", "TBD - needs research\\n", section="## Future Work")

        # Update status across document (expecting exactly 2 occurrences)
        edit_note("reports", "status-report", "find_replace", "In Progress", find_text="Not Started", expected_replacements=2)

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        ValueError: If operation is invalid or required parameters are missing
        SecurityError: If identifier attempts path traversal

    Note:
        Edit operations require exact identifier matches. If unsure, use read_note() or
        search_notes() first to find the correct identifier. The tool provides detailed
        error messages with suggestions if operations fail.
    """
    # Resolve effective default: allow MCP clients to send null for optional int field
    effective_replacements = expected_replacements if expected_replacements is not None else 1
    project = _compose_workspace_project_route(
        workspace=workspace,
        project=project,
        project_id=project_id,
    )

    # Resolve or reject routable identifier prefixes before selecting a client.
    # Trigger: no explicit project/project_id was provided.
    # Why: memory:// URLs are explicit routes, but plain three-segment identifiers
    #   are ambiguous for a mutating tool.
    # Outcome: memory:// can route; plain workspace/project/path matches stop with
    #   guidance instead of silently editing another project.
    if project is None and project_id is None:
        config = ConfigManager().config
        if identifier.strip().startswith("memory://"):
            detected = await detect_project_from_memory_url_prefix(
                identifier,
                config,
                context=context,
            )
        elif _workspace_identifier_discovery_available(
            identifier,
            config,
        ) and is_workspace_qualified_plain_identifier(identifier):
            detected = await detect_project_from_workspace_identifier_prefix(
                identifier,
                config,
                context=context,
            )
            if detected:
                if output_format == "json":
                    return {
                        "title": None,
                        "permalink": None,
                        "file_path": None,
                        "checksum": None,
                        "operation": operation,
                        "fileCreated": False,
                        "error": "AMBIGUOUS_IDENTIFIER",
                        "project": detected,
                    }
                return _format_ambiguous_workspace_identifier_response(
                    identifier=identifier,
                    detected_project=detected,
                )
        else:
            detected = None
        if detected:
            project = detected

    with logfire.span(
        "mcp.tool.edit_note",
        entrypoint="mcp",
        tool_name="edit_note",
        requested_project=project,
        requested_project_id=project_id,
        edit_operation=operation,
        output_format=output_format,
        has_section=bool(section),
        has_find_text=bool(find_text),
        expected_replacements=effective_replacements,
    ):
        async with get_project_client(project, context=context, project_id=project_id) as (
            client,
            active_project,
        ):
            logger.info(
                f"MCP tool call tool=edit_note project={active_project.name} "
                f"identifier={identifier} operation={operation} output_format={output_format}"
            )

            # Validate operation
            valid_operations = [
                "append",
                "prepend",
                "find_replace",
                "replace_section",
                "insert_before_section",
                "insert_after_section",
            ]
            if operation not in valid_operations:
                raise ValueError(
                    f"Invalid operation '{operation}'. Must be one of: {', '.join(valid_operations)}"
                )

            # Validate required parameters for specific operations
            if operation == "find_replace" and not find_text:
                raise ValueError("find_text parameter is required for find_replace operation")
            section_ops = ("replace_section", "insert_before_section", "insert_after_section")
            if operation in section_ops and not section:
                raise ValueError("section parameter is required for section-based operations")

            # Use the PATCH endpoint to edit the entity
            try:
                # Import here to avoid circular import
                from basic_memory.mcp.clients import KnowledgeClient

                # Use typed KnowledgeClient for API calls
                knowledge_client = KnowledgeClient(client, active_project.external_id)
                _, entity_identifier, _ = await resolve_project_and_path(
                    client,
                    identifier,
                    active_project.name,
                    context,
                )

                file_created = False
                entity_id = ""
                result: EntityResponse | None = None

                # Try to resolve the entity; for append/prepend, create it if not found
                try:
                    entity_id = await knowledge_client.resolve_entity(
                        entity_identifier,
                        strict=True,
                    )
                except Exception as resolve_error:
                    # Trigger: entity does not exist yet
                    # Why: append/prepend can meaningfully create a new note from the content,
                    #      while find_replace/replace_section require existing content to modify
                    # Outcome: note is created via the same path as write_note
                    error_msg = str(resolve_error).lower()
                    is_not_found = "entity not found" in error_msg or "not found" in error_msg

                    if is_not_found and operation in ("append", "prepend"):
                        title, directory = _parse_identifier_to_title_and_directory(identifier)

                        # Validate directory path (same security check as write_note)
                        project_path = active_project.home
                        if directory and not validate_project_path(directory, project_path):
                            logger.warning(
                                "Attempted path traversal attack blocked",
                                directory=directory,
                                project=active_project.name,
                            )
                            if output_format == "json":
                                return {
                                    "title": title,
                                    "permalink": None,
                                    "file_path": None,
                                    "checksum": None,
                                    "operation": operation,
                                    "fileCreated": False,
                                    "error": "SECURITY_VALIDATION_ERROR",
                                }
                            return f"# Error\n\nDirectory path '{directory}' is not allowed - paths must stay within project boundaries"

                        entity = Entity(
                            title=title,
                            directory=directory,
                            content_type="text/markdown",
                            content=content,
                        )

                        logger.info(
                            "Creating note via edit_note auto-create",
                            title=title,
                            directory=directory,
                            operation=operation,
                        )
                        result = await knowledge_client.create_entity(entity.model_dump())
                        file_created = True
                    else:
                        # find_replace/replace_section require existing content — re-raise
                        raise resolve_error

                # --- Standard edit path (entity already existed) ---
                if not file_created:
                    # Prepare the edit request data
                    edit_data = {
                        "operation": operation,
                        "content": content,
                    }

                    # Add optional parameters
                    if section:
                        edit_data["section"] = section
                    if find_text:
                        edit_data["find_text"] = find_text
                    if effective_replacements != 1:  # Only send if different from default
                        edit_data["expected_replacements"] = str(effective_replacements)

                    # Call the PATCH endpoint
                    result = await knowledge_client.patch_entity(entity_id, edit_data)

                # --- Format response ---
                # result is always set: either by create_entity (auto-create) or patch_entity (edit)
                assert result is not None
                if file_created:
                    summary = [
                        f"# Created note ({operation})",
                        f"project: {active_project.name}",
                        f"file_path: {result.file_path}",
                        f"permalink: {result.permalink}",
                        f"checksum: {result.checksum[:8] if result.checksum else 'unknown'}",
                        "fileCreated: true",
                    ]
                    lines_added = len(content.split("\n"))
                    summary.append(f"operation: Created note with {lines_added} lines")
                else:
                    summary = [
                        f"# Edited note ({operation})",
                        f"project: {active_project.name}",
                        f"file_path: {result.file_path}",
                        f"permalink: {result.permalink}",
                        f"checksum: {result.checksum[:8] if result.checksum else 'unknown'}",
                    ]

                    # Add operation-specific details
                    if operation == "append":
                        lines_added = len(content.split("\n"))
                        summary.append(f"operation: Added {lines_added} lines to end of note")
                    elif operation == "prepend":
                        lines_added = len(content.split("\n"))
                        summary.append(f"operation: Added {lines_added} lines to beginning of note")
                    elif operation == "find_replace":
                        # For find_replace, we can't easily count replacements from here
                        # since we don't have the original content, but the server handled it
                        summary.append("operation: Find and replace operation completed")
                    elif operation == "replace_section":
                        summary.append(f"operation: Replaced content under section '{section}'")
                    elif operation == "insert_before_section":
                        summary.append(f"operation: Inserted content before section '{section}'")
                    elif operation == "insert_after_section":
                        summary.append(f"operation: Inserted content after section '{section}'")

                # Count observations by category (reuse logic from write_note)
                categories = {}
                if result.observations:
                    for obs in result.observations:
                        categories[obs.category] = categories.get(obs.category, 0) + 1

                    summary.append("\n## Observations")
                    for category, count in sorted(categories.items()):
                        summary.append(f"- {category}: {count}")

                # Count resolved/unresolved relations
                unresolved = 0
                resolved = 0
                if result.relations:
                    unresolved = sum(1 for r in result.relations if not r.to_id)
                    resolved = len(result.relations) - unresolved

                    summary.append("\n## Relations")
                    summary.append(f"- Resolved: {resolved}")
                    if unresolved:
                        summary.append(f"- Unresolved: {unresolved}")

                logger.info(
                    f"MCP tool response: tool=edit_note project={active_project.name} "
                    f"operation={operation} permalink={result.permalink} "
                    f"observations_count={len(result.observations)} "
                    f"relations_count={len(result.relations)} "
                    f"file_created={str(file_created).lower()}"
                )

                if output_format == "json":
                    return {
                        "title": result.title,
                        "permalink": result.permalink,
                        "file_path": result.file_path,
                        "checksum": result.checksum,
                        "operation": operation,
                        "fileCreated": file_created,
                    }

                summary_result = "\n".join(summary)
                return add_project_metadata(summary_result, active_project.name)

            except Exception as e:
                logger.error(f"Error editing note: {e}")
                if output_format == "json":
                    return {
                        "title": None,
                        "permalink": None,
                        "file_path": None,
                        "checksum": None,
                        "operation": operation,
                        "fileCreated": False,
                        "error": str(e),
                    }
                return _format_error_response(
                    str(e),
                    operation,
                    identifier,
                    find_text,
                    effective_replacements,
                    active_project.name,
                )
