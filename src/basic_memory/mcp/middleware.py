from typing import Any
from loguru import logger
from fastmcp.server.middleware import Middleware, MiddlewareContext

from basic_memory.config import ConfigManager
from basic_memory.schemas.project_info import ProjectItem


class ProjectContextMiddleware(Middleware):
    def __init__(self):
        config = ConfigManager().config
        default_project_name = config.default_project
        default_project_path = config.projects[default_project_name]

        self.default_project = ProjectItem(
            name=default_project_name, path=default_project_path, is_default=True
        )
        self.sessions: dict[str, ProjectItem] = {}

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        # Initialize context with default project if not set
        if context.fastmcp_context:
            session_id = context.fastmcp_context.session_id
            project = self.sessions.get(session_id)

            active_project = project or self.default_project

            # store in sessions
            if not project:
                self.sessions[session_id] = active_project

            # set active project on context
            context.fastmcp_context.set_state("active_project", active_project)
            logger.debug(
                f"project context set: session_id={session_id}, active_project={active_project}"
            )

        # call tool
        result = await call_next(context)

        # set global state for if the project changed during tool call
        if context.fastmcp_context:
            session_id = context.fastmcp_context.session_id
            active_project = context.fastmcp_context.get_state("active_project")
            project = self.sessions.get(session_id)
            if project != active_project:
                logger.debug(
                    f"project session saved: session_id={session_id}, active_project={active_project}"
                )
                self.sessions[session_id] = active_project

        return result
