"""Schemas for cloud-related API responses."""

from pydantic import BaseModel, Field


class TenantMountInfo(BaseModel):
    """Response from /tenant/mount/info endpoint."""

    tenant_id: str = Field(..., description="Unique identifier for the tenant")
    bucket_name: str = Field(..., description="S3 bucket name for the tenant")


class MountCredentials(BaseModel):
    """Response from /tenant/mount/credentials endpoint."""

    access_key: str = Field(..., description="S3 access key for mount")
    secret_key: str = Field(..., description="S3 secret key for mount")


class CloudProject(BaseModel):
    """Representation of a cloud project."""

    name: str = Field(..., description="Project name")
    path: str = Field(..., description="Project path on cloud")


class CloudProjectList(BaseModel):
    """Response from /proxy/projects/projects endpoint."""

    projects: list[CloudProject] = Field(default_factory=list, description="List of cloud projects")


class CloudProjectCreateRequest(BaseModel):
    """Request to create a new cloud project."""

    name: str = Field(..., description="Project name")
    path: str = Field(..., description="Project path (permalink)")
    set_default: bool = Field(default=False, description="Set as default project")


class CloudProjectCreateResponse(BaseModel):
    """Response from creating a cloud project."""

    message: str = Field(..., description="Status message about the project creation")
    status: str = Field(..., description="Status of the creation (success or error)")
    default: bool = Field(..., description="True if the project was set as the default")
    old_project: dict | None = Field(None, description="Information about the previous project")
    new_project: dict | None = Field(
        None, description="Information about the newly created project"
    )
