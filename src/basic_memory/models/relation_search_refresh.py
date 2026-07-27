"""Durable reconciliation work for relation-derived search projections."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from basic_memory.models.base import Base


class RelationSearchRefresh(Base):
    """A committed relation change whose source search projection must be refreshed.

    Rows are append-only until a successful refresh retires the exact work items
    it observed. Multiple rows for one entity are intentional: a relation change
    committed during an in-flight refresh must remain visible to a later pass.
    """

    __tablename__ = "relation_search_refresh"
    __table_args__ = (
        Index("ix_relation_search_refresh_project_id", "project_id"),
        Index("ix_relation_search_refresh_entity_id", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entity.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now().astimezone(),
    )
