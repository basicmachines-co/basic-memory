"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_get, call_post, call_delete
from basic_memory.schemas.project_info import (
    ProjectList,
    ProjectStatusResponse,
    ProjectInfoRequest,
)
from basic_memory.utils import generate_permalink


@mcp.tool("list_memory_projects",
    description="""Discovers all configured Basic Memory projects. Essential first step for multi-project environments to identify available knowledge bases.

```yaml
node:
  topic: list_memory_projects - Discovery
  goal: Find all available projects
  insight: Entry point for project selection
  context:
    caching: 60 second TTL
    includes: [status, file_count, last_sync]
```

```baml
class Project {
  name string
  path string
  is_default boolean
  status ("active" | "inactive" | "error")
  file_count int
  last_sync datetime?
}

class ListProjectsOutput {
  projects Project[]
  default_project string?
  total int
}

function list_memory_projects() -> ListProjectsOutput {
  @description("Discover all configured projects")
  @cache_ttl(60)
  @async(true)
}
```

## Usage
```python
projects = list_memory_projects()
for p in projects["projects"]:
    print(f"{p['name']}: {p['path']}")
    if p["is_default"]:
        print("  (default)")
```

Performance: 20-60ms | Cached for 60 seconds"""
)
async def list_memory_projects(context: Context | None = None) -> str:
    """List all available projects with their status."""
    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info("Listing all available projects")

        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

        # Get projects from API
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        if constrained_project:
            result = f"Project: {constrained_project}\n\n"
            result += "Note: This MCP server is constrained to a single project.\n"
            result += "All operations will automatically use this project."
        else:
            # Show all projects with session guidance
            result = "Available projects:\n"

            for project in project_list.projects:
                result += f"• {project.name}\n"

            result += "\n" + "─" * 40 + "\n"
            result += "Next: Ask which project to use for this session.\n"
            result += "Example: 'Which project should I use for this task?'\n\n"
            result += "Session reminder: Track the selected project for all subsequent operations in this conversation.\n"
            result += "The user can say 'switch to [project]' to change projects."

        return result


@mcp.tool("create_memory_project",
    description="""Initializes a new Basic Memory project at the specified location. Creates necessary structure and optionally sets as default project.

```yaml
node:
  topic: create_memory_project - Initialization
  goal: Setup new knowledge base project
  insight: One-time project bootstrap
```

```baml
class CreateProjectInput {
  project_name string @pattern("^[a-z0-9-]+$")
  project_path string @format("path")
  set_default boolean @default(false)
}

class CreateProjectOutput {
  success boolean
  project {name: string, path: string}
  message string
}

function create_memory_project(CreateProjectInput) -> CreateProjectOutput {
  @description("Initialize new project")
  @idempotent(true)
}
```

```python
create_memory_project(
    "research-2024",
    "/Users/me/research/2024",
    set_default=True
)
```"""
)
async def create_memory_project(
    project_name: str, project_path: str, set_default: bool = False, context: Context | None = None
) -> str:
    """Create a new Basic Memory project."""
    async with get_client() as client:
        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        if constrained_project:
            return f'# Error\n\nProject creation disabled - MCP server is constrained to project \'{constrained_project}\'.\nUse the CLI to create projects: `basic-memory project add "{project_name}" "{project_path}"`'

        if context:  # pragma: no cover
            await context.info(f"Creating project: {project_name} at {project_path}")

        # Create the project request
        project_request = ProjectInfoRequest(
            name=project_name, path=project_path, set_default=set_default
        )

        # Call API to create project
        response = await call_post(client, "/projects/projects", json=project_request.model_dump())
        status_response = ProjectStatusResponse.model_validate(response.json())

        result = f"✓ {status_response.message}\n\n"

        if status_response.new_project:
            result += "Project Details:\n"
            result += f"• Name: {status_response.new_project.name}\n"
            result += f"• Path: {status_response.new_project.path}\n"

            if set_default:
                result += "• Set as default project\n"

        result += "\nProject is now available for use in tool calls.\n"
        result += f"Use '{project_name}' as the project parameter in MCP tool calls.\n"

        return result


@mcp.tool(
    description="""Removes a project from Basic Memory configuration. Files remain on disk but project is no longer tracked. Requires re-addition to access content again.

```yaml
node:
  topic: delete_project - Project Removal
  goal: Remove project from configuration
  insight: Files preserved, only tracking removed
  context:
    safety: No file deletion
    reversibility: Re-add project to restore access
```

```baml
class DeleteProjectInput {
  project_name string @description("Project to remove")
}

class DeleteProjectOutput {
  success boolean
  removed_project {name: string, path: string?}
  message string
  warning string @default("Files remain on disk")
}

function delete_project(DeleteProjectInput) -> DeleteProjectOutput {
  @description("Remove project tracking")
  @safe(true) // No file deletion
  @async(true)
}
```

```python
# Remove old project
delete_project("archived-research")
```

Performance: 30-100ms | No files deleted | Reversible"""
)
async def delete_project(project_name: str, context: Context | None = None) -> str:
    """Delete a Basic Memory project."""
    async with get_client() as client:
        # Check if server is constrained to a specific project
        constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        if constrained_project:
            return f"# Error\n\nProject deletion disabled - MCP server is constrained to project '{constrained_project}'.\nUse the CLI to delete projects: `basic-memory project remove \"{project_name}\"`"

        if context:  # pragma: no cover
            await context.info(f"Deleting project: {project_name}")

        # Get project info before deletion to validate it exists
        response = await call_get(client, "/projects/projects")
        project_list = ProjectList.model_validate(response.json())

        # Find the project by name (case-insensitive) or permalink - same logic as switch_project
        project_permalink = generate_permalink(project_name)
        target_project = None
        for p in project_list.projects:
            # Match by permalink (handles case-insensitive input)
            if p.permalink == project_permalink:
                target_project = p
                break
            # Also match by name comparison (case-insensitive)
            if p.name.lower() == project_name.lower():
                target_project = p
                break

        if not target_project:
            available_projects = [p.name for p in project_list.projects]
            raise ValueError(
                f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
            )

        # Call API to delete project using URL encoding for special characters
        from urllib.parse import quote

        encoded_name = quote(target_project.name, safe="")
        response = await call_delete(client, f"/projects/{encoded_name}")
        status_response = ProjectStatusResponse.model_validate(response.json())

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            if hasattr(status_response.old_project, "path"):
                result += f"• Path: {status_response.old_project.path}\n"

        result += "Files remain on disk but project is no longer tracked by Basic Memory.\n"
        result += "Re-add the project to access its content again.\n"

        return result
