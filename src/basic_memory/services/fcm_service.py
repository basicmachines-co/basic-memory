"""Service layer for FCM contract endpoints."""

from uuid import uuid4

from basic_memory.schemas.graph_intelligence import (
    FCMExportFile,
    FCMExportRequest,
    FCMExportResponse,
    FCMGoalRef,
    FCMImportRequest,
    FCMImportResponse,
    FCMNodeDelta,
    FCMNodeState,
    FCMRankActionsRequest,
    FCMRankActionsResponse,
    FCMRecommendation,
    FCMSimulateRequest,
    FCMSimulateResponse,
    FCMStability,
)


class FCMService:
    """FCM contract service.

    Phase 1 keeps deterministic behavior so API and tool surfaces stabilize
    before introducing advanced simulation engines.
    """

    async def simulate(self, request: FCMSimulateRequest) -> FCMSimulateResponse:
        """Return deterministic baseline/projected state vectors."""
        baseline = [FCMNodeState(node_id=action.node_id, state=0.0) for action in request.actions]
        projected = [
            FCMNodeState(node_id=action.node_id, state=action.delta) for action in request.actions
        ]
        deltas = [
            FCMNodeDelta(node_id=action.node_id, delta=action.delta) for action in request.actions
        ]
        return FCMSimulateResponse(
            baseline=baseline,
            projected=projected,
            deltas=deltas,
            stability=FCMStability(
                converged=True,
                iterations_used=min(request.scenario.steps, 5),
                residual=0.0,
            ),
            confidence=0.5,
            explanations=[],
            evidence_refs=[],
        )

    async def rank_actions(self, request: FCMRankActionsRequest) -> FCMRankActionsResponse:
        """Return deterministic ranked actions for a target goal."""
        recommendations = [
            FCMRecommendation(
                action_node_id=f"{request.goal}:action:{idx + 1}",
                expected_goal_delta=0.25 - (idx * 0.01),
                risk_penalty=0.05 + (idx * 0.005),
                net_score=0.20 - (idx * 0.015),
                confidence=0.5,
                rationale=["Contract skeleton recommendation"],
                evidence_refs=[],
            )
            for idx in range(min(request.top_k, 3))
        ]
        return FCMRankActionsResponse(
            goal=FCMGoalRef(node_id=request.goal, label=request.goal),
            recommendations=recommendations,
        )

    async def import_model(self, request: FCMImportRequest) -> FCMImportResponse:
        """Return deterministic import metadata."""
        _ = request
        return FCMImportResponse(
            import_id=str(uuid4()),
            nodes_loaded=0,
            edges_loaded=0,
            warnings=[],
            errors=[],
        )

    async def export_model(self, request: FCMExportRequest) -> FCMExportResponse:
        """Return deterministic export metadata and file descriptors."""
        scope = request.selection.scope
        return FCMExportResponse(
            export_id=str(uuid4()),
            format=request.format,
            files=[
                FCMExportFile(name="nodes.csv", path=f"/tmp/{scope}-nodes.csv"),
                FCMExportFile(name="edges.csv", path=f"/tmp/{scope}-edges.csv"),
            ],
            node_count=0,
            edge_count=0,
            metadata={"scope": scope},
        )
