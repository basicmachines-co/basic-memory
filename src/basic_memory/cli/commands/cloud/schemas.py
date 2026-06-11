"""Pydantic schemas for Basic Memory Cloud API responses.

These schemas mirror the API response models from basic-memory-cloud
for type-safe parsing of API responses in CLI commands.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BucketSnapshotFileInfo(BaseModel):
    """File info from snapshot browse response."""

    key: str
    size: int
    last_modified: datetime
    etag: str | None = None


class BucketSnapshotBrowseResponse(BaseModel):
    """Response from browsing snapshot contents."""

    files: list[BucketSnapshotFileInfo]
    prefix: str
    snapshot_version: str


class BucketSnapshotResponse(BaseModel):
    """Response model for bucket snapshot data."""

    id: UUID
    bucket_name: str
    snapshot_version: str
    name: str
    description: str | None
    auto: bool
    created_at: datetime
    created_by: UUID | None = None


class BucketSnapshotListResponse(BaseModel):
    """Response from listing bucket snapshots."""

    snapshots: list[BucketSnapshotResponse]
    total: int


class BucketSnapshotRestoreResponse(BaseModel):
    """Response from restore operation."""

    restored: list[str]
    snapshot_version: str
    snapshot_id: UUID


class PublicShareResponse(BaseModel):
    """Response model for a public share link.

    Mirrors PublicShareResponse in basic-memory-cloud
    (apps/cloud/.../schemas/public_share_schemas.py).
    """

    id: UUID
    token: str
    project_name: str
    note_permalink: str
    note_external_id: str
    enabled: bool
    expires_at: datetime | None
    share_url: str
    view_count: int
    last_viewed_at: datetime | None
    created_at: datetime


class PublicShareListResponse(BaseModel):
    """Response from listing public shares."""

    shares: list[PublicShareResponse]
    total: int
