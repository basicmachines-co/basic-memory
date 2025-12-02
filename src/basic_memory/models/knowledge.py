"""Knowledge graph models."""

from datetime import datetime
from basic_memory.utils import ensure_timezone_aware
from typing import Optional

from sqlalchemy import (
    Integer,
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
    DateTime,
    Index,
    JSON,
    Float,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from basic_memory.models.base import Base
from basic_memory.utils import generate_permalink


class Entity(Base):
    """Core entity in the knowledge graph.

    Entities represent semantic nodes maintained by the AI layer. Each entity:
    - Has a unique numeric ID (database-generated)
    - Maps to a file on disk
    - Maintains a checksum for change detection
    - Tracks both source file and semantic properties
    - Belongs to a specific project
    """

    __tablename__ = "entity"
    __table_args__ = (
        # Regular indexes
        Index("ix_entity_type", "entity_type"),
        Index("ix_entity_title", "title"),
        Index("ix_entity_created_at", "created_at"),  # For timeline queries
        Index("ix_entity_updated_at", "updated_at"),  # For timeline queries
        Index("ix_entity_project_id", "project_id"),  # For project filtering
        # Project-specific uniqueness constraints
        Index(
            "uix_entity_permalink_project",
            "permalink",
            "project_id",
            unique=True,
            sqlite_where=text("content_type = 'text/markdown' AND permalink IS NOT NULL"),
        ),
        Index(
            "uix_entity_file_path_project",
            "file_path",
            "project_id",
            unique=True,
        ),
    )

    # Core identity
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String)
    entity_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    content_type: Mapped[str] = mapped_column(String)

    # Project reference
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), nullable=False)

    # Normalized path for URIs - required for markdown files only
    permalink: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Actual filesystem relative path
    file_path: Mapped[str] = mapped_column(String, index=True)
    # checksum of file
    checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # File metadata for sync
    # mtime: file modification timestamp (Unix epoch float) for change detection
    mtime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # size: file size in bytes for quick change detection
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Metadata and tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now().astimezone()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now().astimezone(),
        onupdate=lambda: datetime.now().astimezone(),
    )

    # Relationships
    project = relationship("Project", back_populates="entities")
    observations = relationship(
        "Observation", back_populates="entity", cascade="all, delete-orphan"
    )
    outgoing_relations = relationship(
        "Relation",
        back_populates="from_entity",
        foreign_keys="[Relation.from_id]",
        cascade="all, delete-orphan",
    )
    incoming_relations = relationship(
        "Relation",
        back_populates="to_entity",
        foreign_keys="[Relation.to_id]",
        cascade="all, delete-orphan",
    )

    @property
    def relations(self):
        """Get all relations (incoming and outgoing) for this entity."""
        return self.incoming_relations + self.outgoing_relations

    @property
    def is_markdown(self):
        """Check if the entity is a markdown file."""
        return self.content_type == "text/markdown"

    def __getattribute__(self, name):
        """Override attribute access to ensure datetime fields are timezone-aware."""
        value = super().__getattribute__(name)

        # Ensure datetime fields are timezone-aware
        if name in ("created_at", "updated_at") and isinstance(value, datetime):
            return ensure_timezone_aware(value)

        return value

    def __repr__(self) -> str:
        return f"Entity(id={self.id}, name='{self.title}', type='{self.entity_type}', checksum='{self.checksum}')"


class Observation(Base):
    """An observation about an entity.

    Observations are atomic facts or notes about an entity.
    """

    __tablename__ = "observation"
    __table_args__ = (
        Index("ix_observation_entity_id", "entity_id"),  # Add FK index
        Index("ix_observation_category", "category"),  # Add category index
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String, nullable=False, default="note")
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[list[str]]] = mapped_column(
        JSON, nullable=True, default=list, server_default="[]"
    )

    # Relationships
    entity = relationship("Entity", back_populates="observations")

    @property
    def permalink(self) -> str:
        """Create synthetic permalink for the observation.

        We can construct these because observations are always defined in
        and owned by a single entity.

        Content is truncated to 200 chars to stay under PostgreSQL's
        btree index limit of 2704 bytes.
        """
        # Truncate content to avoid exceeding PostgreSQL's btree index limit
        content_for_permalink = self.content[:200] if len(self.content) > 200 else self.content
        return generate_permalink(
            f"{self.entity.permalink}/observations/{self.category}/{content_for_permalink}"
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"Observation(id={self.id}, entity_id={self.entity_id}, content='{self.content}')"


class Relation(Base):
    """A directed relation between two entities."""

    __tablename__ = "relation"
    __table_args__ = (
        UniqueConstraint("from_id", "to_id", "relation_type", name="uix_relation_from_id_to_id"),
        UniqueConstraint(
            "from_id", "to_name", "relation_type", name="uix_relation_from_id_to_name"
        ),
        Index("ix_relation_type", "relation_type"),
        Index("ix_relation_from_id", "from_id"),  # Add FK indexes
        Index("ix_relation_to_id", "to_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), index=True)
    from_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    to_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("entity.id", ondelete="CASCADE"), nullable=True
    )
    to_name: Mapped[str] = mapped_column(String)
    relation_type: Mapped[str] = mapped_column(String)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    from_entity = relationship(
        "Entity", foreign_keys=[from_id], back_populates="outgoing_relations"
    )
    to_entity = relationship("Entity", foreign_keys=[to_id], back_populates="incoming_relations")

    @property
    def permalink(self) -> str:
        """Create relation permalink showing the semantic connection.

        Format: source/relation_type/target
        Example: "specs/search/implements/features/search-ui"
        """
        # Only create permalinks when both source and target have permalinks
        from_permalink = self.from_entity.permalink or self.from_entity.file_path

        if self.to_entity:
            to_permalink = self.to_entity.permalink or self.to_entity.file_path
            return generate_permalink(f"{from_permalink}/{self.relation_type}/{to_permalink}")
        return generate_permalink(f"{from_permalink}/{self.relation_type}/{self.to_name}")

    def __repr__(self) -> str:
        return f"Relation(id={self.id}, from_id={self.from_id}, to_id={self.to_id}, to_name={self.to_name}, type='{self.relation_type}')"  # pragma: no cover
