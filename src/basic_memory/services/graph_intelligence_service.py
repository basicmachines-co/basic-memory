"""Service layer for graph intelligence contract endpoints."""

from datetime import datetime, timezone
from uuid import uuid4

from basic_memory.schemas.graph_intelligence import (
    GraphHealthMetrics,
    GraphHealthResponse,
    GraphImpactItem,
    GraphImpactRequest,
    GraphImpactResponse,
    GraphImpactSummary,
    GraphImpactTarget,
    GraphLineagePath,
    GraphLineageRequest,
    GraphLineageResponse,
    GraphNodeRef,
    GraphPathEdge,
    GraphReindexResponse,
)


def _normalize_memory_ref(value: str) -> str:
    """Normalize user input into a memory:// reference string."""
    if value.startswith("memory://"):
        return value
    return f"memory://{value}"


def _normalize_node_id(value: str) -> str:
    """Return a stable node id for contract skeleton outputs."""
    return value.removeprefix("memory://")


class GraphIntelligenceService:
    """Graph intelligence contract service.

    Phase 1 behavior is intentionally deterministic and lightweight so routing,
    clients, and contract tests can ship before deeper traversal engines.
    """

    async def lineage(self, request: GraphLineageRequest) -> GraphLineageResponse:
        """Return a deterministic lineage payload for the requested root/goal."""
        root_ref = _normalize_memory_ref(request.start)
        root = GraphNodeRef(
            id=_normalize_node_id(root_ref),
            title=_normalize_node_id(root_ref),
            permalink=_normalize_node_id(root_ref),
        )

        nodes = [root]
        edges: list[GraphPathEdge] = []
        if request.goal:
            goal_ref = _normalize_memory_ref(request.goal)
            nodes.append(
                GraphNodeRef(
                    id=_normalize_node_id(goal_ref),
                    title=_normalize_node_id(goal_ref),
                    permalink=_normalize_node_id(goal_ref),
                )
            )
            edges.append(GraphPathEdge(relation="related_to", direction="outgoing"))

        path = GraphLineagePath(
            path_id=f"path-{uuid4()}",
            nodes=nodes,
            edges=edges,
            deterministic_path_score=1.0 if request.goal else 0.5,
            confidence=0.5,
            evidence_refs=[root_ref],
        )

        return GraphLineageResponse(
            root=root,
            paths=[path],
            generated_at=datetime.now(timezone.utc),
        )

    async def impact(self, request: GraphImpactRequest) -> GraphImpactResponse:
        """Return a deterministic impact preview payload."""
        target_id = _normalize_node_id(_normalize_memory_ref(request.target))
        affected = [
            GraphImpactItem(
                id=f"{target_id}:neighbor:1",
                title=f"{target_id} dependent",
                distance=min(request.horizon, 1),
                impact_score=0.55,
                confidence=0.5,
                reasons=["Connected via typed relation in contract skeleton"],
                evidence_refs=[_normalize_memory_ref(request.target)],
            )
        ]
        if not request.include_reasons:
            affected[0].reasons = []

        return GraphImpactResponse(
            target=GraphImpactTarget(id=target_id, title=target_id),
            affected=affected,
            summary=GraphImpactSummary(total_considered=1, total_returned=1),
        )

    async def health(self, scope: str | None, timeframe: str | None) -> GraphHealthResponse:
        """Return deterministic baseline health metrics."""
        _ = (scope, timeframe)
        return GraphHealthResponse(
            metrics=GraphHealthMetrics(
                orphan_rate=0.0,
                stale_central_nodes=0,
                overloaded_hubs=0,
                contradiction_candidates=0,
            ),
            issues=[],
            computed_at=datetime.now(timezone.utc),
        )

    async def start_reindex_job(self) -> GraphReindexResponse:
        """Create reindex job metadata for queued responses."""
        return GraphReindexResponse(
            job_id=str(uuid4()),
            status="queued",
            scheduled_at=datetime.now(timezone.utc),
        )
