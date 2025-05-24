"""Project model for Basic Memory."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Index,
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
        Index("ix_project_path", "path"),
        Index("ix_project_created_at", "created_at"),
        Index("ix_project_updated_at", "updated_at"),
    )

    # Core identity
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # URL-friendly identifier generated from name
    permalink: Mapped[str] = mapped_column(String, unique=True)

    # Filesystem path to project directory
    path: Mapped[str] = mapped_column(String)

    # Status flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[Optional[bool]] = mapped_column(
        Boolean, default=None, unique=True, nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Define relationships to entities, observations, and relations
    # These relationships will be established once we add project_id to those models
    entities = relationship("Entity", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Project(id={self.id}, name='{self.name}', permalink='{self.permalink}', path='{self.path}')"


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
