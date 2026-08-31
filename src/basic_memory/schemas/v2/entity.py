"""V2 entity and project schemas with ID-first design."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict

from basic_memory.schemas.response import ObservationResponse, RelationResponse


class EntityResolveRequest(BaseModel):
    """Request to resolve a string identifier inside one target project.

    Supports resolution of:
    - Permalinks (e.g., "specs/search")
    - Titles (e.g., "Search Specification")
    - File paths (e.g., "specs/search.md")

    When source_path is provided, resolution prefers notes closer to the source
    within the same project. Qualified references never change the target project.
    """

    identifier: str = Field(
        ...,
        description="Entity identifier to resolve (permalink, title, or file path)",
        min_length=1,
        max_length=500,
    )
    source_path: Optional[str] = Field(
        None,
        description="Path of the source file containing the link (for context-aware resolution)",
        max_length=500,
    )
    strict: bool = Field(
        False,
        description="If True, only exact matches are allowed (no fuzzy search fallback)",
    )


class EntityResolveResponse(BaseModel):
    """Response from project-scoped entity resolution.

    The owning project always matches the target project in the request route.
    """

    external_id: str = Field(..., description="External UUID (primary API identifier)")
    entity_id: int = Field(..., description="Numeric entity ID (internal identifier)")
    project_external_id: str = Field(..., description="External UUID of the owning project")
    permalink: Optional[str] = Field(None, description="Entity permalink")
    file_path: str = Field(..., description="Relative file path")
    title: str = Field(..., description="Entity title")
    resolution_method: Literal["external_id", "permalink", "title", "path", "search"] = Field(
        ..., description="How the identifier was resolved"
    )


class LinkResolveRequest(BaseModel):
    """Request to resolve a wikilink from one explicit source project."""

    identifier: str = Field(
        ...,
        description="Wikilink or identifier to resolve from the source project",
        min_length=1,
        max_length=500,
    )
    source_path: Optional[str] = Field(
        None,
        description="Path of the source note within the route project",
        max_length=500,
    )
    strict: bool = Field(
        False,
        description="If True, only exact matches are allowed (no fuzzy search fallback)",
    )


class LinkResolveResponse(BaseModel):
    """Response from source-aware wikilink resolution.

    Hosted callers must separately authorize ``target_project_external_id`` before exposing
    target metadata. Authorization of the source project in the request route is not sufficient.
    """

    external_id: str = Field(..., description="External UUID of the resolved entity")
    entity_id: int = Field(..., description="Numeric entity ID (internal identifier)")
    target_project_external_id: str = Field(
        ...,
        description="External UUID of the resolved target project; authorize it separately",
    )
    permalink: Optional[str] = Field(None, description="Resolved entity permalink")
    file_path: str = Field(..., description="Resolved entity path in the target project")
    title: str = Field(..., description="Resolved entity title")
    resolution_method: Literal["external_id", "permalink", "title", "path", "search"] = Field(
        ..., description="How the wikilink was resolved"
    )


class IndexFileRequest(BaseModel):
    """Request to index a single markdown file that exists on disk.

    Used as a recovery path when an identifier fails resolution but maps to a
    file written directly to disk that the watcher has not indexed yet (#581).
    """

    file_path: str = Field(
        ...,
        description="Markdown file path to index (relative to project root)",
        min_length=1,
        max_length=500,
    )


class MoveEntityRequestV2(BaseModel):
    """V2 request schema for moving an entity to a new file location.

    In V2 API, the entity ID is provided in the URL path, so this request
    only needs the destination path.
    """

    destination_path: str = Field(
        ...,
        description="New file path for the entity (relative to project root)",
        min_length=1,
        max_length=500,
    )


class MoveDirectoryRequestV2(BaseModel):
    """V2 request schema for moving an entire directory to a new location.

    This moves all entities within a source directory to a destination directory
    while maintaining project consistency and updating database references.
    """

    source_directory: str = Field(
        ...,
        description="Source directory path (relative to project root)",
        min_length=1,
        max_length=500,
    )
    destination_directory: str = Field(
        ...,
        description="Destination directory path (relative to project root)",
        min_length=1,
        max_length=500,
    )


class DeleteDirectoryRequestV2(BaseModel):
    """V2 request schema for deleting all entities in a directory.

    This deletes all entities within a directory, removing them from the
    database and file system.
    """

    directory: str = Field(
        ...,
        description="Directory path to delete (relative to project root)",
        min_length=1,
        max_length=500,
    )


class EntityResponseV2(BaseModel):
    """V2 entity response with external_id as the primary API identifier.

    This response format emphasizes the external_id (UUID) as the primary API identifier,
    with the numeric id maintained for internal reference.
    """

    # External UUID first - this is the primary API identifier in v2
    external_id: str = Field(..., description="External UUID (primary API identifier)")
    # Internal numeric ID
    id: int = Field(..., description="Numeric entity ID (internal identifier)")

    # Core entity fields
    title: str = Field(..., description="Entity title")
    note_type: str = Field(..., description="Note type (from frontmatter 'type' field)")
    content_type: str = Field(default="text/markdown", description="Content MIME type")

    # Secondary identifiers (for compatibility and convenience)
    permalink: Optional[str] = Field(None, description="Entity permalink (may change)")
    file_path: str = Field(..., description="Relative file path (may change)")

    # Content and metadata
    content: Optional[str] = Field(None, description="Entity content")
    entity_metadata: Optional[Dict] = Field(None, description="Entity metadata")

    # Relationships
    observations: List[ObservationResponse] = Field(
        default_factory=list, description="Entity observations"
    )
    relations: List[RelationResponse] = Field(default_factory=list, description="Entity relations")

    # Timestamps
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # User tracking (cloud only, null for local/CLI usage)
    created_by: Optional[str] = Field(None, description="User profile ID of creator")
    last_updated_by: Optional[str] = Field(None, description="User profile ID of last editor")

    # Accepted note_content state. These are present for markdown note routes that
    # use the accepted-note runtime and null for legacy indexed entity responses.
    db_version: Optional[int] = Field(None, description="Accepted note_content DB version")
    db_checksum: Optional[str] = Field(None, description="Accepted note_content checksum")
    file_version: Optional[int] = Field(None, description="Materialized file version")
    file_checksum: Optional[str] = Field(None, description="Materialized file checksum")
    file_write_status: Optional[str] = Field(None, description="Materialized file write status")
    last_source: Optional[str] = Field(None, description="Last accepted note_content source")
    file_updated_at: Optional[datetime] = Field(
        None, description="Timestamp of the last materialized file update"
    )
    last_materialization_error: Optional[str] = Field(
        None, description="Most recent note materialization error"
    )
    sync_error: Optional[str] = Field(None, description="Current note sync error")

    # Slice metadata (SPEC-47 / #1403). Set only when the request asked for a
    # section=, lines=, or max_tokens= slice, in which case `content` is the
    # slice and line numbers count over the full stored document, frontmatter
    # included. All None on unsliced responses, so every other producer of this
    # model is untouched.
    section: Optional[str] = Field(
        None,
        description="Canonical heading path of the returned section slice "
        "(bracket-suffixed when a duplicate heading was addressed)",
    )
    content_start_line: Optional[int] = Field(
        None, description="Document-absolute 1-indexed first line of the returned content"
    )
    content_end_line: Optional[int] = Field(
        None, description="Document-absolute 1-indexed last line of the returned content"
    )
    content_total_lines: Optional[int] = Field(
        None, description="Total line count of the full stored document"
    )
    content_truncated: Optional[bool] = Field(
        None, description="Whether max_tokens truncated the returned content"
    )
    content_continue_line: Optional[int] = Field(
        None, description="First line omitted by truncation; resume line reads here"
    )

    # V2-specific metadata
    api_version: Literal["v2"] = Field(
        default="v2", description="API version (always 'v2' for this response)"
    )

    model_config = ConfigDict(from_attributes=True)


class ProjectResolveRequest(BaseModel):
    """Request to resolve a project identifier to a project ID.

    Supports resolution of:
    - Project names (e.g., "my-project")
    - Permalinks (e.g., "my-project")
    """

    identifier: str = Field(
        ...,
        description="Project identifier to resolve (name or permalink)",
        min_length=1,
        max_length=255,
    )


class ProjectResolveResponse(BaseModel):
    """Response from project identifier resolution.

    Returns the project ID and associated metadata for the resolved project.
    """

    external_id: str = Field(..., description="External UUID (primary API identifier)")
    project_id: int = Field(..., description="Numeric project ID (internal identifier)")
    name: str = Field(..., description="Project name")
    permalink: str = Field(..., description="Project permalink")
    path: str = Field(..., description="Project file path")
    is_active: bool = Field(..., description="Whether the project is active")
    is_default: bool = Field(..., description="Whether the project is the default")
    resolution_method: Literal["external_id", "name", "permalink"] = Field(
        ..., description="How the identifier was resolved"
    )
