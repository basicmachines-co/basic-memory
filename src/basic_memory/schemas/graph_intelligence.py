"""Schemas for Local+ graph intelligence and FCM contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Graph contracts ---


class GraphLineageRequest(BaseModel):
    """Request contract for graph lineage queries."""

    start: str
    goal: str | None = None
    max_hops: int = Field(default=4, ge=1, le=6)
    relation_filters: list[str] = Field(default_factory=list)


class GraphNodeRef(BaseModel):
    """Minimal graph node descriptor."""

    id: str
    title: str
    permalink: str | None = None


class GraphPathEdge(BaseModel):
    """Edge descriptor for lineage paths."""

    relation: str
    direction: Literal["outgoing", "incoming"]


class GraphLineagePath(BaseModel):
    """Single lineage path with scores and provenance."""

    path_id: str
    nodes: list[GraphNodeRef] = Field(default_factory=list)
    edges: list[GraphPathEdge] = Field(default_factory=list)
    deterministic_path_score: float
    confidence: float
    evidence_refs: list[str] = Field(default_factory=list)


class GraphLineageResponse(BaseModel):
    """Response contract for graph lineage queries."""

    root: GraphNodeRef
    paths: list[GraphLineagePath] = Field(default_factory=list)
    generated_at: datetime


class GraphImpactRequest(BaseModel):
    """Request contract for impact-radius queries."""

    target: str
    horizon: int = Field(ge=1, le=4)
    relation_filters: list[str] = Field(default_factory=list)
    include_reasons: bool = True


class GraphImpactTarget(BaseModel):
    """Impact response target descriptor."""

    id: str
    title: str


class GraphImpactItem(BaseModel):
    """Affected node entry for impact responses."""

    id: str
    title: str
    distance: int
    impact_score: float
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class GraphImpactSummary(BaseModel):
    """Summary counters for impact responses."""

    total_considered: int
    total_returned: int


class GraphImpactResponse(BaseModel):
    """Response contract for impact-radius queries."""

    target: GraphImpactTarget
    affected: list[GraphImpactItem] = Field(default_factory=list)
    summary: GraphImpactSummary


class GraphHealthMetrics(BaseModel):
    """Top-level graph health metrics."""

    orphan_rate: float
    stale_central_nodes: int
    overloaded_hubs: int
    contradiction_candidates: int


class GraphHealthIssue(BaseModel):
    """Actionable graph-health issue entry."""

    issue_type: Literal[
        "orphan",
        "stale_central",
        "overloaded_hub",
        "contradiction_candidate",
    ]
    entity_id: str
    severity: Literal["low", "medium", "high"]
    reason: str
    suggested_action: str
    confidence: float | None = None


class GraphHealthResponse(BaseModel):
    """Response contract for health checks."""

    metrics: GraphHealthMetrics
    issues: list[GraphHealthIssue] = Field(default_factory=list)
    computed_at: datetime


class GraphReindexRequest(BaseModel):
    """Request contract for graph reindex scheduling."""

    mode: Literal["full", "incremental"] = "incremental"
    reason: str | None = None


class GraphReindexResponse(BaseModel):
    """Response contract for graph reindex scheduling."""

    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    scheduled_at: datetime


# --- FCM contracts ---


class FCMAction(BaseModel):
    """Action delta for simulation input."""

    node_id: str
    delta: float


class FCMScenario(BaseModel):
    """Simulation runtime configuration."""

    steps: int = 12
    activation: Literal["tanh", "sigmoid", "bounded_linear"] = "tanh"
    decay: float = 0.05


class FCMClampRule(BaseModel):
    """Clamp bounds for selected nodes."""

    node_id: str
    min: float
    max: float


class FCMSimulateRequest(BaseModel):
    """Request contract for FCM simulation."""

    actions: list[FCMAction]
    scenario: FCMScenario = Field(default_factory=FCMScenario)
    clamp_rules: list[FCMClampRule] = Field(default_factory=list)


class FCMNodeState(BaseModel):
    """Node state in baseline/projected vectors."""

    node_id: str
    state: float


class FCMNodeDelta(BaseModel):
    """Node delta entry in simulation output."""

    node_id: str
    delta: float


class FCMStability(BaseModel):
    """Simulation stability metadata."""

    converged: bool
    iterations_used: int
    residual: float


class FCMInfluencer(BaseModel):
    """Top influencer entry for explanation payload."""

    source: str
    weight: float


class FCMExplanation(BaseModel):
    """Per-node explanation payload."""

    node_id: str
    top_influencers: list[FCMInfluencer] = Field(default_factory=list)


class FCMSimulateResponse(BaseModel):
    """Response contract for FCM simulation."""

    baseline: list[FCMNodeState] = Field(default_factory=list)
    projected: list[FCMNodeState] = Field(default_factory=list)
    deltas: list[FCMNodeDelta] = Field(default_factory=list)
    stability: FCMStability
    confidence: float
    explanations: list[FCMExplanation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class FCMRankConstraints(BaseModel):
    """Constraint set for action ranking."""

    max_negative_impact: float | None = None
    required_tags: list[str] = Field(default_factory=list)
    disallowed_nodes: list[str] = Field(default_factory=list)


class FCMRankActionsRequest(BaseModel):
    """Request contract for FCM action ranking."""

    goal: str
    constraints: FCMRankConstraints = Field(default_factory=FCMRankConstraints)
    top_k: int = Field(default=10, ge=1, le=25)


class FCMGoalRef(BaseModel):
    """Goal descriptor for ranking output."""

    node_id: str
    label: str


class FCMRecommendation(BaseModel):
    """Ranked intervention candidate."""

    action_node_id: str
    expected_goal_delta: float
    risk_penalty: float
    net_score: float
    confidence: float
    rationale: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class FCMRankActionsResponse(BaseModel):
    """Response contract for action ranking."""

    goal: FCMGoalRef
    recommendations: list[FCMRecommendation] = Field(default_factory=list)


class FCMImportRequest(BaseModel):
    """Request contract for model import."""

    source: str
    format: Literal["csv_bundle_v1"] = "csv_bundle_v1"
    merge_mode: Literal["replace", "upsert"] = "upsert"


class FCMImportResponse(BaseModel):
    """Response contract for model import."""

    import_id: str
    nodes_loaded: int
    edges_loaded: int
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FCMExportSelection(BaseModel):
    """Scope selection for model export."""

    scope: Literal["all", "tag", "subgraph"] = "all"
    tag: str | None = None
    seed_nodes: list[str] = Field(default_factory=list)


class FCMExportRequest(BaseModel):
    """Request contract for model export."""

    format: Literal["csv_bundle_v1"] = "csv_bundle_v1"
    selection: FCMExportSelection = Field(default_factory=FCMExportSelection)


class FCMExportFile(BaseModel):
    """Single file descriptor in an export response."""

    name: str
    path: str


class FCMExportResponse(BaseModel):
    """Response contract for model export."""

    export_id: str
    format: Literal["csv_bundle_v1"]
    files: list[FCMExportFile] = Field(default_factory=list)
    node_count: int
    edge_count: int
    metadata: dict[str, Any] | None = None
