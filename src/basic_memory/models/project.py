"""Project model for Basic Memory."""

import uuid
from datetime import datetime, UTC
from typing import override, Optional

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from basic_memory.models.base import Base
from basic_memory.utils import generate_permalink


class Project(Base):
    """Project model for Basic Memory.

    A project represents a collection of knowledge entities that are grouped together.
    Projects are stored in the app-level database and provide context for all knowledge
    operations.
    """

    __tablename__ = "project"
    __table_args__ = (
        # Regular indexes
        Index("ix_project_name", "name", unique=True),
        Index("ix_project_permalink", "permalink", unique=True),
        Index("ix_project_external_id", "external_id", unique=True),
        Index("ix_project_path", "path"),
        Index("ix_project_created_at", "created_at"),
        Index("ix_project_updated_at", "updated_at"),
    )

    # Core identity
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # External UUID for API references - stable identifier that won't change
    external_id: Mapped[str] = mapped_column(String, unique=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # URL-friendly identifier generated from name
    permalink: Mapped[str] = mapped_column(String, unique=True)

    # Filesystem path to project directory
    path: Mapped[str] = mapped_column(String)

    # Status flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[Optional[bool]] = mapped_column(Boolean, default=None, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Sync optimization - scan watermark tracking
    last_scan_timestamp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_file_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Strict, project-local ordering for accepted durable changes. This is the
    # generic partition head that future event-journal projectors can reuse; it
    # is deliberately not derived from timestamps, webhooks, or scan activity.
    partition_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # Define relationships to entities, observations, and relations
    # These relationships will be established once we add project_id to those models
    entities = relationship("Entity", back_populates="project", cascade="all, delete-orphan")
    accepted_note_changes = relationship(
        "AcceptedProjectNoteChange",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="AcceptedProjectNoteChange.partition_position",
        passive_deletes=True,
    )

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return f"Project(id={self.id}, external_id='{self.external_id}', name='{self.name}', permalink='{self.permalink}', path='{self.path}')"


@event.listens_for(Project, "before_insert")
@event.listens_for(Project, "before_update")
def set_project_permalink(mapper, connection, project):
    """Generate URL-friendly permalink for the project if needed.

    This event listener ensures the permalink is always derived from the name,
    even if the name changes.
    """
    # If the name changed or permalink is empty, regenerate permalink
    if not project.permalink or project.permalink != generate_permalink(project.name):
        project.permalink = generate_permalink(project.name)


class AcceptedProjectNoteChange(Base):
    """Durable evidence for one accepted note mutation in project order.

    The row intentionally retains note identity and path values instead of an
    entity foreign key: delete evidence must survive after the entity is gone.
    """

    __tablename__ = "accepted_project_note_change"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "partition_position",
            name="uq_accepted_project_note_change_project_position",
        ),
        Index(
            "ix_accepted_project_note_change_project_materialized",
            "project_id",
            "materialized_at",
        ),
        Index(
            "ix_accepted_project_note_change_note_external_id",
            "note_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_external_id: Mapped[str] = mapped_column(String, nullable=False)
    partition_position: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note_external_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    previous_file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    db_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    db_checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_user_profile_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    materialized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    project = relationship("Project", back_populates="accepted_note_changes")
