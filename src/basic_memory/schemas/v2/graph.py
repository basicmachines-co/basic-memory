"""Graph visualization schemas for the knowledge graph endpoint."""

from typing import Optional

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A node in the knowledge graph visualization."""

    external_id: str = Field(..., description="Entity external ID (UUID)")
    title: str = Field(..., description="Entity title")
    note_type: Optional[str] = Field(None, description="Note type (e.g., note, spec, task)")
    file_path: str = Field(..., description="Relative file path")


class GraphEdge(BaseModel):
    """An edge in the knowledge graph visualization."""

    from_id: str = Field(..., description="External ID of source entity")
    to_id: str = Field(..., description="External ID of target entity")
    relation_type: str = Field(..., description="Type of relation")


class GraphResponse(BaseModel):
    """Complete knowledge graph for visualization."""

    nodes: list[GraphNode] = Field(default_factory=list, description="All entities as nodes")
    edges: list[GraphEdge] = Field(
        default_factory=list, description="All resolved relations as edges"
    )


class OrphanEntitiesResponse(BaseModel):
    """Entities that have no incoming or outgoing relations in the knowledge graph."""

    entities: list[GraphNode] = Field(
        default_factory=list, description="Entities with no relations"
    )
    total: int = Field(..., description="Total count of orphan entities")
