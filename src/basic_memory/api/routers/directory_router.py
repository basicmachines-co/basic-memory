"""Router for directory tree operations."""

from typing import List, Optional

from fastapi import APIRouter, Query

from basic_memory.deps import DirectoryServiceDep, ProjectIdDep
from basic_memory.schemas.directory import DirectoryNode

router = APIRouter(prefix="/directory", tags=["directory"])


@router.get("/tree", response_model=DirectoryNode)
async def get_directory_tree(
    directory_service: DirectoryServiceDep,
    project_id: ProjectIdDep,
):
    """Get hierarchical directory structure from the knowledge base.

    Args:
        directory_service: Service for directory operations
        project_id: ID of the current project

    Returns:
        DirectoryNode representing the root of the hierarchical tree structure
    """
    # Get a hierarchical directory tree for the specific project
    tree = await directory_service.get_directory_tree()

    # Return the hierarchical tree
    return tree


@router.get("/list", response_model=List[DirectoryNode])
async def list_directory(
    directory_service: DirectoryServiceDep,
    project_id: ProjectIdDep,
    dir_name: str = Query("/", description="Directory path to list"),
    depth: int = Query(1, ge=1, le=10, description="Recursion depth (1-10)"),
    file_name_glob: Optional[str] = Query(
        None, description="Glob pattern for filtering file names"
    ),
):
    """List directory contents with filtering and depth control.

    Args:
        directory_service: Service for directory operations
        project_id: ID of the current project
        dir_name: Directory path to list (default: root "/")
        depth: Recursion depth (1-10, default: 1 for immediate children only)
        file_name_glob: Optional glob pattern for filtering file names (e.g., "*.md", "*meeting*")

    Returns:
        List of DirectoryNode objects matching the criteria
    """
    # Get directory listing with filtering
    nodes = await directory_service.list_directory(
        dir_name=dir_name,
        depth=depth,
        file_name_glob=file_name_glob,
    )

    return nodes
