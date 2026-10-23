"""Route a bm_* tool's target into its project and resolve notes exactly.

Every bm_* tool starts the same way: classify the `project` argument, let a
'<project>/path' prefix on the target choose or confirm the project, and open a
client routed to it. Routing is `resolve_project_path_route`, so a bm_* tool
accepts exactly the project-qualified paths tools return.

Resolution is exact. A target that names no note is a NOT_FOUND refusal that
points to `bm_search`; there is no fuzzy fallback, because a tool that edits or
deletes must never act on a near match.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from httpx import AsyncClient, HTTPStatusError, Response

from basic_memory.mcp.async_client import local_project_path
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.project_context import (
    AmbiguousMountError,
    ProjectPathRoute,
    ProjectPrefixConflictError,
    UnqualifiedPathRefusedError,
    get_project_client,
    resolve_project_path_route,
)
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.tool_params import OutputFormat, parse_project, routing_arguments
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.schemas.v2.entity import EntityResolveResponse
from basic_memory.utils import generate_permalink, validate_project_path


type TargetKind = Literal["path", "identifier"]


@dataclass(frozen=True, slots=True)
class ProjectTarget:
    """A target routed into its project, with a client for that project.

    `route.path` is the project-relative target: the input with any project prefix
    removed, "" when the input named the project itself.
    """

    client: AsyncClient
    project: ProjectItem
    route: ProjectPathRoute

    @property
    def paths(self) -> "PathQualifier":
        """Re-qualifies response paths the way this target addressed its project."""
        return PathQualifier.for_route(self.route)


@asynccontextmanager
async def open_project_target(
    target: str,
    project: str | None,
    context: Context | None,
    *,
    kind: TargetKind = "path",
    output_format: OutputFormat = "text",
) -> AsyncIterator[ProjectTarget]:
    """Route `target` and yield a client for the project it lives in.

    `kind` says what the target is. A "path" names a location in the project (a
    directory, or a file to create) and must not climb out of it. An "identifier"
    names an existing note by path, permalink, title or id; it is resolved by the
    project-scoped API, so it cannot leave the project, and an exact title such as
    'Home ~ Plan' is not a path to check.
    """
    project_name, project_id = routing_arguments(parse_project(project))
    # Routing refuses some targets outright; each refusal keeps the routing's own
    # message (it names the projects involved) in the caller's output format.
    try:
        route = await resolve_project_path_route(
            target, project=project_name, project_id=project_id, context=context
        )
    except ProjectPrefixConflictError as error:
        raise refusal("INVALID_ARGUMENTS", str(error), output_format=output_format) from error
    except UnqualifiedPathRefusedError as error:
        raise refusal(
            "INVALID_ARGUMENTS",
            str(error),
            try_instead="'<project>/path', or pass `project`",
            output_format=output_format,
        ) from error
    except AmbiguousMountError as error:
        raise refusal("AMBIGUOUS", str(error), output_format=output_format) from error
    async with get_project_client(route.project, context=context, project_id=route.project_id) as (
        client,
        active_project,
    ):
        # Trigger: a path target climbs out of the project ('../x', an absolute path, '~').
        # Why: every bm_* tool, including the ones that write and delete, must stay
        #      inside its project. Remote projects are checked lexically; only a local
        #      project's root is resolved against the filesystem.
        # Outcome: an OUTSIDE_PROJECT refusal before any read or write.
        # A leading '/' anchors at the project root ('/' or '/specs'), never the
        # filesystem root, so it is dropped before the check.
        project_relative = memory_url_path(route.path).lstrip("/")
        if kind == "path" and not validate_project_path(
            project_relative, local_project_path(client, active_project)
        ):
            raise refusal(
                "OUTSIDE_PROJECT",
                f"'{target}' is outside the project. Paths must stay within the project.",
                try_instead="a path relative to the project root, such as 'notes/plan.md'",
                output_format=output_format,
            )
        yield ProjectTarget(client=client, project=active_project, route=route)


def _api_answer(error: ToolError) -> Response | None:
    """The API's HTTP answer behind a typed-client failure, or None if it never answered."""
    cause = error.__cause__
    return cause.response if isinstance(cause, HTTPStatusError) else None


def _detail(answer: Response) -> str:
    """The `detail` FastAPI put in an error answer, as text."""
    if "application/json" not in answer.headers.get("content-type", ""):
        return answer.text
    detail = answer.json().get("detail")
    return detail if isinstance(detail, str) else str(detail)


async def resolve_note(
    target: ProjectTarget,
    identifier: str,
    *,
    output_format: OutputFormat = "text",
) -> EntityResolveResponse:
    """Resolve the routed target to its note, exactly.

    The response carries the note's external id, file path and title, which is
    what a tool needs to decide how to read or change it.

    `identifier` is the caller's original spelling, used in the refusal so the
    model sees what it asked for.
    """
    # Trigger: the input was a bare project name ('research' or 'research/').
    # Why: routing consumed the whole identifier; there is no note left to resolve.
    # Outcome: refuse with the call that lists the project instead.
    if target.route.stripped and not target.route.path:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{identifier}' names a project, not a note.",
            try_instead=f"bm_list(path='{identifier.rstrip('/')}/')",
            output_format=output_format,
        )

    knowledge_client = KnowledgeClient(target.client, target.project.external_id)
    try:
        return await knowledge_client.resolve_entity_response(target.route.path, strict=True)
    except ToolError as error:
        answer = _api_answer(error)
        match answer.status_code if answer is not None else None:
            case 404:
                raise refusal(
                    "NOT_FOUND",
                    f"No note matches '{identifier}' in project '{target.project.name}'.",
                    try_instead=f"bm_search(query='{identifier}')",
                    output_format=output_format,
                ) from error
            # Several notes match exactly (two notes with one title); the API's
            # detail names them, and the caller has to pick one.
            case 409 if answer is not None:
                raise refusal(
                    "AMBIGUOUS",
                    _detail(answer),
                    try_instead="a file path or permalink that names one note",
                    output_format=output_format,
                ) from error
            case _:
                raise


# --- Round-trip coherence ---
# A path a tool returns must be a path the resolver accepts. When a call addresses
# its project in the path ('research/notes'), the project prefix is stripped before
# the project-scoped API sees it, so the API answers in project-relative paths.
# Handing those back unchanged breaks the round trip: '/notes' would be refused as
# unqualified, or open a different project mounted as 'notes'.
#
# PathQualifier decides once whether and with what to re-qualify; each response
# shape names which of its fields are addresses. Rewriting by key name anywhere in
# a payload also rewrote a note's own frontmatter `file_path:` key, so position,
# not spelling, decides what is an address.


@dataclass(frozen=True, slots=True)
class PathQualifier:
    """Re-attaches the project prefix a request's path carried.

    `prefix` is None when the caller named the project some other way (the
    `project` parameter, or a single-project session); project-relative paths
    are then already the ones the caller can pass back.
    """

    prefix: str | None

    @classmethod
    def for_route(cls, route: ProjectPathRoute) -> "PathQualifier":
        if not route.stripped or route.project is None:
            return cls(prefix=None)
        # The permalink form is the spelling `ls "/"` advertises and the resolver
        # normalizes to, so it round-trips for display names too ('My Research').
        return cls(prefix=generate_permalink(route.project))

    def path(self, value: str) -> str:
        """One path, re-qualified with the slash shape it came in."""
        if self.prefix is None:
            return value
        if value.startswith("/"):
            return f"/{self.prefix}" if value == "/" else f"/{self.prefix}{value}"
        return f"{self.prefix}/{value}" if value else self.prefix

    def note(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A note payload: `file_path` is its address.

        `frontmatter` is the note's own YAML and comes back byte for byte, even
        when it has a `file_path:` key; `permalink` is an identity with its own
        canonical form. Neither is touched.
        """
        if self.prefix is None:
            return payload
        return {**payload, "file_path": self.path(payload["file_path"])}

    def listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A directory listing: each node's `directory_path` and `file_path`."""
        if self.prefix is None:
            return payload
        return {**payload, "nodes": [self._node(node) for node in payload["nodes"]]}

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A search response: each hit's `file_path` (#1435)."""
        if self.prefix is None:
            return payload
        return {
            **payload,
            "results": [
                {**row, "file_path": self.path(row["file_path"])} for row in payload["results"]
            ],
        }

    def _node(self, node: dict[str, Any]) -> dict[str, Any]:
        qualified = {**node, "directory_path": self.path(node["directory_path"])}
        # file_path is optional on DirectoryNode: directory rows carry no file.
        if node.get("file_path") is not None:
            qualified["file_path"] = self.path(node["file_path"])
        if node.get("children"):
            qualified["children"] = [self._node(child) for child in node["children"]]
        return qualified
