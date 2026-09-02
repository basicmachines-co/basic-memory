"""Knowledge graph models."""

import hashlib
import uuid
from datetime import datetime
from basic_memory.utils import ensure_timezone_aware
from typing import Any, override, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
    DateTime,
    Index,
    JSON,
    Float,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from basic_memory.models.base import Base
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_CONTENT_TYPE
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
        Index("ix_note_type", "note_type"),
        Index("ix_entity_title", "title"),
        Index("ix_entity_external_id", "external_id", unique=True),
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
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    # External UUID for API references - stable identifier that won't change
    external_id: Mapped[str] = mapped_column(String, unique=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String)
    note_type: Mapped[str] = mapped_column(String)
    entity_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
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
    )

    # Who created this entity (cloud user_profile_id UUID, null for local/CLI usage)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    # Who last modified this entity (cloud user_profile_id UUID, null for local/CLI usage)
    last_updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

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
    # No delete cascade here, and the asymmetry with outgoing_relations is the point.
    # An entity owns the relations it declares; it does not own the ones other entities
    # declare at it. Deleting this entity unresolves the inbound rows (to_id -> NULL) and
    # leaves to_name holding the source's original link text -- the same state the indexer
    # produces for a link to a note that does not exist yet. passive_deletes hands the job
    # to the database's ON DELETE SET NULL instead of having the ORM null the rows itself.
    incoming_relations = relationship(
        "Relation",
        back_populates="to_entity",
        foreign_keys="[Relation.to_id]",
        passive_deletes=True,
    )
    note_content = relationship(
        "NoteContent",
        back_populates="entity",
        cascade="all, delete-orphan",
        uselist=False,
    )
    sections = relationship("NoteSection", back_populates="entity", cascade="all, delete-orphan")
    time_assertions = relationship(
        "MemoryTimeIndex", back_populates="entity", cascade="all, delete-orphan"
    )

    @validates("created_at", "updated_at")
    def _normalize_semantic_timestamp(self, attribute_name: str, value: datetime) -> datetime:
        """Keep SQLite's timezone-naive storage faithful to the represented instant."""
        del attribute_name
        return ensure_timezone_aware(value).astimezone()

    @property
    def relations(self):
        """Get all relations (incoming and outgoing) for this entity."""
        return self.incoming_relations + self.outgoing_relations

    @property
    def is_markdown(self):
        """Check if the entity is a markdown file."""
        return self.content_type == RUNTIME_MARKDOWN_CONTENT_TYPE

    @override
    def __getattribute__(self, name):
        """Override attribute access to ensure datetime fields are timezone-aware."""
        value = super().__getattribute__(name)

        # Ensure datetime fields are timezone-aware
        if name in ("created_at", "updated_at") and isinstance(value, datetime):
            return ensure_timezone_aware(value)

        return value

    @override
    def __repr__(self) -> str:
        return f"Entity(id={self.id}, external_id='{self.external_id}', name='{self.title}', type='{self.note_type}', checksum='{self.checksum}')"


class NoteContent(Base):
    """Materialized markdown content and sync state for a note entity."""

    __tablename__ = "note_content"
    __table_args__ = (
        CheckConstraint(
            "file_write_status IN ("
            "'pending', "
            "'writing', "
            "'synced', "
            "'failed', "
            "'external_change_detected'"
            ")",
            name="ck_note_content_file_write_status",
        ),
        Index("ix_note_content_project_id", "project_id"),
        Index("ix_note_content_file_path", "file_path"),
        Index("ix_note_content_external_id", "external_id", unique=True),
    )

    # Core identity mirrored from entity for hot note reads
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)

    # Materialized content version tracked in the tenant database
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    db_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    db_checksum: Mapped[str] = mapped_column(String, nullable=False)

    # File materialization state tracked against the latest write attempts
    file_version: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    file_checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_write_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    last_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now().astimezone(),
        onupdate=lambda: datetime.now().astimezone(),
    )
    file_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_materialization_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_materialization_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    entity = relationship("Entity", back_populates="note_content")

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"NoteContent(entity_id={self.entity_id}, external_id='{self.external_id}', "
            f"file_path='{self.file_path}', file_write_status='{self.file_write_status}')"
        )


class NoteFileVacate(Base):
    """A source path vacated by a move whose physical object is pending cleanup.

    A ``move_note`` is DB-first: the entity's ``file_path`` flips to the destination while the
    physical source object is deleted out of band. This row is the durable, index-time-queryable
    proof that the source path was vacated *by a move* — so the indexer can tell a move's lingering
    source object (a ghost source: skip re-import) from a legitimate byte-identical copy (index as
    new). Written atomically with the move; cleared when the source object is deleted
    (basic-memory-cloud#1601).
    """

    __tablename__ = "note_file_vacate"
    __table_args__ = (
        # One outstanding vacate per source path; the index-time lookup keys on it.
        UniqueConstraint("project_id", "file_path", name="uix_note_file_vacate_project_file_path"),
        Index("ix_note_file_vacate_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The moved entity now living at the destination. Preserve the marker as a checksum-backed
    # tombstone if that entity is deleted before the physical source cleanup finishes.
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("entity.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The vacated source path (project-relative POSIX), the key the indexer checks.
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    # Guard for the physical delete; may be None when the source was never materialized.
    file_checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now().astimezone(),
    )

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"NoteFileVacate(project_id={self.project_id}, entity_id={self.entity_id}, "
            f"file_path='{self.file_path}')"
        )


class NoteSection(Base):
    """One heading-bounded span of a note body.

    Sections are an index into canonical markdown, never a copy of it: rows carry
    body-relative line numbers and utf-8 byte offsets plus the heading path that
    addresses the span. Like observations, they are a derived projection rebuilt
    under the note_content generation fence on every (re)index and removed with
    the entity (SPEC-47 / #1403).
    """

    __tablename__ = "note_section"
    __table_args__ = (
        Index("ix_note_section_entity_id", "entity_id"),
        Index(
            "ix_note_section_entity_path",
            "entity_id",
            "heading_path_digest",
            "duplicate_index",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    heading: Mapped[str] = mapped_column(Text)
    level: Mapped[int] = mapped_column(Integer)
    # Full "Parent/Child" path kept as un-indexed text for display; the lookup
    # index keys on its fixed-width digest because arbitrary heading text can
    # exceed PostgreSQL's 2704-byte btree index-row limit (same guard as
    # Observation.permalink below).
    heading_path: Mapped[str] = mapped_column(Text)
    heading_path_digest: Mapped[str] = mapped_column(String(64))
    duplicate_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Body-relative coordinates: 1-indexed inclusive lines, utf-8 byte offsets
    # with end_offset exclusive. Body-relative keeps rows valid when frontmatter
    # normalization rewrites the file head without touching the body (#1090).
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)

    entity = relationship("Entity", back_populates="sections")

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"NoteSection(id={self.id}, entity_id={self.entity_id}, "
            f"heading_path='{self.heading_path}[{self.duplicate_index}]', "
            f"lines={self.start_line}-{self.end_line})"
        )


def observation_permalink_tail(category: str | None, content: str) -> str:
    """The part of an observation's permalink that distinguishes it within its note.

    This is the single definition of what makes two observations of one note share an
    address, and it is deliberately shared with the writer that assigns
    `Observation.duplicate_index`: an ordinal only disambiguates if it is counted over
    exactly the identity the permalink is built from. Computing the two separately is the
    defect this function exists to prevent -- rebuilding the permalink format inline is
    what diverged from the search index for long observations (#929).

    Note what is *not* here. A qualifier (`@effective[...]`) and a `(context)` are peeled
    off the line before the observation is stored, so neither reaches this string, and two
    observations that differ only in one of them arrive identical. That is not an oversight
    to correct by stuffing them back in: the peel is the feature, and valid time is its own
    projection rather than an observation column. The note still distinguishes such lines,
    so the *address* must too, which is what the ordinal counted over this tail supplies.

    Slug aliasing is why the count keys on this generated text rather than on the raw
    values: `Foo Bar` and `foo-bar` are different content that generate one permalink, so
    an ordinal counted over raw content would leave them colliding.

    Content is truncated to 200 chars to stay under PostgreSQL's btree index limit of
    2704 bytes.
    """
    if len(content) > 200:
        # Trigger: content exceeds the 200-char budget imposed by PostgreSQL's
        # 2704-byte btree index row limit, so the permalink can only carry a prefix.
        # Why: two distinct observations with the same category and an identical
        # 200-char prefix would collide on the same synthetic permalink, and the
        # search index (permalink-keyed upsert) silently drops the second one.
        # Outcome: a short stable digest of the FULL content disambiguates
        # truncated permalinks while staying well under the index limit.
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        content_for_permalink = f"{content[:200]}-{digest}"
    else:
        content_for_permalink = content
    return generate_permalink(f"observations/{category}/{content_for_permalink}")


class Observation(Base):
    """An observation about an entity.

    Observations are atomic facts or notes about an entity.
    """

    __tablename__ = "observation"
    __table_args__ = (
        Index("ix_observation_entity_id", "entity_id"),  # Add FK index
        Index("ix_observation_category", "category"),  # Add category index
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String, nullable=False, default="note")
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[list[str]]] = mapped_column(
        JSON, nullable=True, default=list, server_default="[]"
    )
    # Which of the note's same-identity observations this one is, in document order.
    # See `observation_permalink_tail` for why an ordinal is needed at all and why it is
    # stored rather than derived: `permalink` is read on *detached* instances, long after
    # the session that could have looked at this row's siblings has closed.
    duplicate_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    # Relationships
    entity = relationship("Entity", back_populates="observations")

    @property
    def permalink(self) -> str:
        """Create synthetic permalink for the observation.

        We can construct these because observations are always defined in
        and owned by a single entity.

        `duplicate_index` is what keeps the address faithful when one note says the same
        thing twice. It is 0 for the first observation carrying a given identity, so the
        overwhelming majority of permalinks are byte-identical to what they have always
        been; only the second and later twins gain a trailing ordinal.
        """
        base = generate_permalink(
            f"{self.entity.permalink}/{observation_permalink_tail(self.category, self.content)}"
        )
        if not self.duplicate_index:
            return base
        return f"{base}/{self.duplicate_index}"

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return f"Observation(id={self.id}, entity_id={self.entity_id}, content='{self.content}')"


class MemoryTimeIndex(Base):
    """One authored temporal assertion, projected into queryable scalar columns.

    A note can say *when a statement is true of the world*, not merely when the file
    was edited::

        - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.

    That qualifier is canonical markdown. This table is its derived projection,
    rebuilt under the note_content generation fence on every (re)index and removed
    with the entity, exactly like observations and sections (SPEC-82). It is never a
    second source of temporal truth: reindexing from the markdown reproduces it.

    The table is generic on purpose. ``source_type``/``source_id`` address whatever
    carries the assertion -- observations in this MVP -- and match the ``(type, id)``
    pair of the corresponding search row, which is what lets a valid-time filter narrow
    search results to the individual observation that was in force. ``source_id``
    deliberately carries no foreign key: it points into a different table per
    ``source_type``. Lifecycle is carried instead by ``entity_id``'s cascade plus the
    fenced replace, the same two mechanisms note_section relies on.

    Bounds are stored as canonical fixed-width text rather than DATE/TIMESTAMP columns:

    * A date bound is a calendar date and must never acquire a time of day or a
      timezone. SQLAlchemy's SQLite ``DateTime`` silently discards an offset, storing
      the wrong instant -- exactly the false precision the spec forbids.
    * ``basic_memory.temporal`` canonicalizes every bound to a fixed-width form
      (``YYYY-MM-DD``; ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` in UTC), so byte-lexicographic
      order *is* chronological order and one identical SQL predicate serves both
      dialects.

    Native PostgreSQL ``daterange``/``tstzrange`` columns stay available as a later
    addition: they would be generated from these columns, which remain the portable
    source of truth.
    """

    __tablename__ = "memory_time_index"
    __table_args__ = (
        # The valid-time predicate selects (source_type, source_id) after filtering on
        # project, kind, and axis, so this index both drives the scan and covers its
        # projection. project_id leads it, which is why the column carries no separate
        # index of its own the way sibling projection tables do.
        Index(
            "ix_memory_time_index_lookup",
            "project_id",
            "time_kind",
            "range_axis",
            "source_type",
            "source_id",
        ),
        # Fenced replace deletes by entity_id, and the cascade follows the same column.
        Index("ix_memory_time_index_entity_id", "entity_id"),
        CheckConstraint(
            "range_axis IN ('date', 'instant')",
            name="ck_memory_time_index_range_axis",
        ),
        # The empty range has no endpoints at all; representing it with bounds would
        # make two rows describe the same interval two different ways.
        CheckConstraint(
            "NOT is_empty OR (lower_value IS NULL AND upper_value IS NULL)",
            name="ck_memory_time_index_empty_has_no_bounds",
        ),
        # PostgreSQL's rule: an unbounded side cannot be inclusive, because there is no
        # endpoint to include. Enforcing it here keeps the query predicates from having
        # to defend against a bound state the domain value cannot produce.
        CheckConstraint(
            "(lower_value IS NOT NULL OR NOT lower_inclusive) "
            "AND (upper_value IS NOT NULL OR NOT upper_inclusive)",
            name="ck_memory_time_index_unbounded_is_exclusive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"))
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    # Addresses the row that carried the qualifier, and equals the search row's
    # (type, id) pair. No FK: the target table varies with source_type.
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int] = mapped_column(Integer)
    time_kind: Mapped[str] = mapped_column(String(32))
    range_axis: Mapped[str] = mapped_column(String(16))
    # Canonical lexical bounds; NULL means unbounded on that side.
    lower_value: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    upper_value: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    lower_inclusive: Mapped[bool] = mapped_column(Boolean)
    upper_inclusive: Mapped[bool] = mapped_column(Boolean)
    is_empty: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    extractor: Mapped[str] = mapped_column(String(32))
    # The qualifier exactly as authored, so a result can explain itself in the
    # author's own precision rather than in the canonical form.
    source_text: Mapped[str] = mapped_column(Text)
    # `metadata` is reserved on the declarative base, so the column follows
    # Entity.entity_metadata's naming convention for the same reason.
    assertion_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    entity = relationship("Entity", back_populates="time_assertions")

    @override
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MemoryTimeIndex(id={self.id}, entity_id={self.entity_id}, "
            f"source={self.source_type}:{self.source_id}, kind='{self.time_kind}', "
            f"range='{self.source_text}')"
        )


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
        Index(
            "ix_relation_project_from_generation",
            "project_id",
            "from_id",
            "generation",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # pyright: ignore [reportIncompatibleVariableOverride]
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), index=True)
    from_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity.id", ondelete="CASCADE"))
    # SET NULL, not CASCADE. Deleting the target must not erase the record that some other
    # entity still points here -- the wikilink is still sitting in that note's markdown, so
    # a graph that forgets the edge reports a clean bill of health over a broken link.
    # from_id above keeps CASCADE: an entity really does own the relations it declares.
    to_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("entity.id", ondelete="SET NULL"), nullable=True
    )
    to_name: Mapped[str] = mapped_column(String)
    relation_type: Mapped[str] = mapped_column(String)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Relation rows are a projection of one accepted note-content generation.
    # Zero is reserved for rows written by pre-generation binaries during a rolling deploy.
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

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

    @override
    def __repr__(self) -> str:
        return f"Relation(id={self.id}, from_id={self.from_id}, to_id={self.to_id}, to_name={self.to_name}, type='{self.relation_type}')"  # pragma: no cover
