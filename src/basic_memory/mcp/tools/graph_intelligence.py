"""MCP tools for graph intelligence and FCM contracts."""

from typing import Any, Literal

from fastmcp import Context

from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import mcp
from basic_memory.schemas.graph_intelligence import (
    FCMExportRequest,
    FCMImportRequest,
    FCMRankActionsRequest,
    FCMSimulateRequest,
    GraphImpactRequest,
    GraphLineageRequest,
    GraphReindexRequest,
)


def _format_lineage_text(result: dict[str, Any]) -> str:
    root = result["root"]["title"]
    path_count = len(result.get("paths", []))
    return f"# Graph Lineage\n\nRoot: {root}\nPaths: {path_count}"


def _format_impact_text(result: dict[str, Any]) -> str:
    target = result["target"]["title"]
    affected = len(result.get("affected", []))
    return f"# Graph Impact\n\nTarget: {target}\nAffected: {affected}"


def _format_health_text(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    return (
        "# Graph Health\n\n"
        f"- orphan_rate: {metrics['orphan_rate']}\n"
        f"- stale_central_nodes: {metrics['stale_central_nodes']}\n"
        f"- overloaded_hubs: {metrics['overloaded_hubs']}\n"
        f"- contradiction_candidates: {metrics['contradiction_candidates']}"
    )


def _format_fcm_simulate_text(result: dict[str, Any]) -> str:
    deltas = len(result.get("deltas", []))
    converged = result["stability"]["converged"]
    return f"# FCM Simulation\n\nDeltas: {deltas}\nConverged: {converged}"


def _format_fcm_rank_text(result: dict[str, Any]) -> str:
    goal = result["goal"]["label"]
    count = len(result.get("recommendations", []))
    return f"# FCM Action Ranking\n\nGoal: {goal}\nRecommendations: {count}"


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def graph_lineage(
    start: str,
    goal: str | None = None,
    max_hops: int = 4,
    relation_filters: list[str] | None = None,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Get lineage paths from a start node toward an optional goal."""
    from basic_memory.mcp.clients import GraphClient

    request = GraphLineageRequest(
        start=start,
        goal=goal,
        max_hops=max_hops,
        relation_filters=relation_filters or [],
    )
    async with get_project_client(project, workspace, context) as (client, active_project):
        graph_client = GraphClient(client, active_project.external_id)
        result = await graph_client.lineage(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return _format_lineage_text(payload)
    return payload


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def graph_impact(
    target: str,
    horizon: int,
    relation_filters: list[str] | None = None,
    include_reasons: bool = True,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Get impact radius from a target node."""
    from basic_memory.mcp.clients import GraphClient

    request = GraphImpactRequest(
        target=target,
        horizon=horizon,
        relation_filters=relation_filters or [],
        include_reasons=include_reasons,
    )
    async with get_project_client(project, workspace, context) as (client, active_project):
        graph_client = GraphClient(client, active_project.external_id)
        result = await graph_client.impact(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return _format_impact_text(payload)
    return payload


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def graph_health(
    scope: str | None = None,
    timeframe: str | None = None,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Get graph health metrics and issues."""
    from basic_memory.mcp.clients import GraphClient

    async with get_project_client(project, workspace, context) as (client, active_project):
        graph_client = GraphClient(client, active_project.external_id)
        result = await graph_client.health(scope=scope, timeframe=timeframe)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return _format_health_text(payload)
    return payload


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
async def fcm_simulate(
    actions: list[dict[str, Any]],
    scenario: dict[str, Any] | None = None,
    clamp_rules: list[dict[str, Any]] | None = None,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Run an FCM simulation with optional scenario controls."""
    from basic_memory.mcp.clients import FCMClient

    request = FCMSimulateRequest.model_validate(
        {
            "actions": actions,
            "scenario": scenario or {},
            "clamp_rules": clamp_rules or [],
        }
    )
    async with get_project_client(project, workspace, context) as (client, active_project):
        fcm_client = FCMClient(client, active_project.external_id)
        result = await fcm_client.simulate(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return _format_fcm_simulate_text(payload)
    return payload


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def fcm_rank_actions(
    goal: str,
    constraints: dict[str, Any] | None = None,
    top_k: int = 10,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Rank intervention actions for an FCM goal node."""
    from basic_memory.mcp.clients import FCMClient

    request = FCMRankActionsRequest.model_validate(
        {
            "goal": goal,
            "constraints": constraints or {},
            "top_k": top_k,
        }
    )
    async with get_project_client(project, workspace, context) as (client, active_project):
        fcm_client = FCMClient(client, active_project.external_id)
        result = await fcm_client.rank_actions(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return _format_fcm_rank_text(payload)
    return payload


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
async def fcm_import_model(
    source: str,
    format: Literal["csv_bundle_v1"] = "csv_bundle_v1",
    merge_mode: Literal["replace", "upsert"] = "upsert",
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Import an FCM model from an external source."""
    from basic_memory.mcp.clients import FCMClient

    request = FCMImportRequest(source=source, format=format, merge_mode=merge_mode)
    async with get_project_client(project, workspace, context) as (client, active_project):
        fcm_client = FCMClient(client, active_project.external_id)
        result = await fcm_client.import_model(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return (
            "# FCM Import\n\n"
            f"Import ID: {payload['import_id']}\n"
            f"Nodes Loaded: {payload['nodes_loaded']}\n"
            f"Edges Loaded: {payload['edges_loaded']}"
        )
    return payload


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
async def fcm_export_model(
    format: Literal["csv_bundle_v1"] = "csv_bundle_v1",
    selection: dict[str, Any] | None = None,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Export an FCM model selection."""
    from basic_memory.mcp.clients import FCMClient

    request = FCMExportRequest.model_validate(
        {
            "format": format,
            "selection": selection or {},
        }
    )
    async with get_project_client(project, workspace, context) as (client, active_project):
        fcm_client = FCMClient(client, active_project.external_id)
        result = await fcm_client.export_model(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return (
            "# FCM Export\n\n"
            f"Export ID: {payload['export_id']}\n"
            f"Node Count: {payload['node_count']}\n"
            f"Edge Count: {payload['edge_count']}"
        )
    return payload


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
async def graph_reindex(
    mode: Literal["full", "incremental"] = "incremental",
    reason: str | None = None,
    project: str | None = None,
    workspace: str | None = None,
    output_format: Literal["json", "text"] = "json",
    context: Context | None = None,
) -> dict[str, Any] | str:
    """Queue a graph reindex for the active project."""
    from basic_memory.mcp.clients import GraphClient

    request = GraphReindexRequest(mode=mode, reason=reason)
    async with get_project_client(project, workspace, context) as (client, active_project):
        graph_client = GraphClient(client, active_project.external_id)
        result = await graph_client.reindex(request)
    payload = result.model_dump(mode="json")
    if output_format == "text":
        return f"# Graph Reindex\n\nJob ID: {payload['job_id']}\nStatus: {payload['status']}"
    return payload
