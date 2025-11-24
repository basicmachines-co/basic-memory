"""V2 resource schemas for file content operations."""

from pydantic import BaseModel, Field


class CreateResourceRequest(BaseModel):
    """Request to create a new resource file.

    File path is required for new resources since we need to know where
    to create the file.
    """

    file_path: str = Field(
        ...,
        description="Path to create the file, relative to project root",
        min_length=1,
        max_length=500,
    )
    content: str = Field(..., description="File content to write")


class UpdateResourceRequest(BaseModel):
    """Request to update an existing resource by entity ID.

    Only content is required - the file path is already known from the entity.
    Optionally can update the file_path to move the file.
    """

    content: str = Field(..., description="File content to write")
    file_path: str | None = Field(
        None,
        description="Optional new file path to move the resource",
        min_length=1,
        max_length=500,
    )


class ResourceResponse(BaseModel):
    """Response from resource operations."""

    entity_id: int = Field(..., description="Entity ID of the resource")
    file_path: str = Field(..., description="File path of the resource")
    checksum: str = Field(..., description="File content checksum")
    size: int = Field(..., description="File size in bytes")
    created_at: float = Field(..., description="Creation timestamp")
    modified_at: float = Field(..., description="Modification timestamp")
