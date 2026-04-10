"""Schemas for cloud-related API responses."""

from typing import Literal

from pydantic import BaseModel, Field

type ProjectVisibility = Literal["workspace", "shared", "private"]


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
    """Response from /proxy/v2/projects endpoint."""

    projects: list[CloudProject] = Field(default_factory=list, description="List of cloud projects")


class CloudProjectCreateRequest(BaseModel):
    """Request to create a new cloud project."""

    name: str = Field(..., description="Project name")
    path: str = Field(..., description="Project path (permalink)")
    set_default: bool = Field(default=False, description="Set as default project")
    visibility: ProjectVisibility = Field(
        default="workspace",
        description="Project visibility for team workspaces",
    )


class CloudProjectCreateResponse(BaseModel):
    """Response from creating a cloud project."""

    message: str = Field(..., description="Status message about the project creation")
    status: str = Field(..., description="Status of the creation (success or error)")
    default: bool = Field(..., description="True if the project was set as the default")
    old_project: dict | None = Field(None, description="Information about the previous project")
    new_project: dict | None = Field(
        None, description="Information about the newly created project"
    )


class WorkspaceInfo(BaseModel):
    """Workspace entry from /workspaces/ endpoint."""

    tenant_id: str = Field(..., description="Workspace tenant identifier")
    workspace_type: str = Field(..., description="Workspace type (personal or organization)")
    name: str = Field(..., description="Workspace display name")
    role: str = Field(..., description="Current user's role in the workspace")
    organization_id: str | None = Field(None, description="Organization ID for org workspaces")
    has_active_subscription: bool = Field(
        default=False, description="Whether the workspace has an active subscription"
    )


class WorkspaceListResponse(BaseModel):
    """Response from /workspaces/ endpoint."""

    workspaces: list[WorkspaceInfo] = Field(
        default_factory=list, description="Available workspaces"
    )
    count: int = Field(default=0, description="Number of available workspaces")
    current_workspace_id: str | None = Field(
        default=None, description="Current workspace tenant ID when available"
    )


class CloudProjectIndexStatus(BaseModel):
    """Index freshness summary for one cloud project."""

    project_name: str = Field(..., description="Project name")
    project_id: int = Field(..., description="Project database identifier")
    last_scan_timestamp: float | None = Field(
        default=None, description="Last scan timestamp from project metadata"
    )
    last_file_count: int | None = Field(default=None, description="Last observed file count")
    current_file_count: int = Field(..., description="Current markdown file count")
    total_entities: int = Field(..., description="Current markdown entity count")
    total_note_content_rows: int = Field(..., description="Rows present in note_content")
    note_content_synced: int = Field(..., description="Files fully materialized into note_content")
    note_content_pending: int = Field(..., description="Pending note_content rows")
    note_content_failed: int = Field(..., description="Failed note_content rows")
    note_content_external_changes: int = Field(
        ..., description="Rows flagged with external file changes"
    )
    total_indexed_entities: int = Field(..., description="Files represented in search_index")
    embedding_opt_out_entities: int = Field(..., description="Files opted out of vector embeddings")
    embeddable_indexed_entities: int = Field(
        ..., description="Indexed files eligible for vector embeddings"
    )
    total_entities_with_chunks: int = Field(..., description="Embeddable files with vector chunks")
    total_chunks: int = Field(..., description="Vector chunk row count")
    total_embeddings: int = Field(..., description="Vector embedding row count")
    orphaned_chunks: int = Field(..., description="Chunks missing embeddings")
    vector_tables_exist: bool = Field(..., description="Whether vector tables exist")
    materialization_current: bool = Field(
        ..., description="Whether note content matches the current file set"
    )
    search_current: bool = Field(..., description="Whether search coverage is current")
    embeddings_current: bool = Field(..., description="Whether embedding coverage is current")
    project_current: bool = Field(..., description="Whether all freshness checks are current")
    reindex_recommended: bool = Field(..., description="Whether a reindex is recommended")
    reindex_reason: str | None = Field(default=None, description="Reason a reindex is recommended")


class CloudTenantIndexStatusResponse(BaseModel):
    """Index freshness summary for all projects in one cloud tenant."""

    tenant_id: str = Field(..., description="Workspace tenant identifier")
    fly_app_name: str = Field(..., description="Cloud tenant application identifier")
    email: str | None = Field(default=None, description="Owner email when available")
    projects: list[CloudProjectIndexStatus] = Field(
        default_factory=list, description="Per-project freshness summaries"
    )
    error: str | None = Field(default=None, description="Tenant-level lookup error")
