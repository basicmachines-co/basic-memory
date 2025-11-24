"""V2 Directory Router - ID-based directory tree operations.

This router provides directory structure browsing for projects using
integer project IDs instead of name-based identifiers.

Key improvements:
- Direct project lookup via integer primary keys
- Consistent with other v2 endpoints
- Better performance through indexed queries
"""

from typing import List, Optional

from fastapi import APIRouter, Query

from basic_memory.deps import DirectoryServiceV2Dep, ProjectIdPathDep
from basic_memory.schemas.directory import DirectoryNode

router = APIRouter(prefix="/directory", tags=["directory-v2"])


@router.get("/tree", response_model=DirectoryNode, response_model_exclude_none=True)
async def get_directory_tree(
    directory_service: DirectoryServiceV2Dep,
    project_id: ProjectIdPathDep,
):
    """Get hierarchical directory structure from the knowledge base.

    Args:
        directory_service: Service for directory operations
        project_id: Numeric project ID

    Returns:
        DirectoryNode representing the root of the hierarchical tree structure
    """
    # Get a hierarchical directory tree for the specific project
    tree = await directory_service.get_directory_tree()

    # Return the hierarchical tree
    return tree


@router.get("/structure", response_model=DirectoryNode, response_model_exclude_none=True)
async def get_directory_structure(
    directory_service: DirectoryServiceV2Dep,
    project_id: ProjectIdPathDep,
):
    """Get folder structure for navigation (no files).

    Optimized endpoint for folder tree navigation. Returns only directory nodes
    without file metadata. For full tree with files, use /directory/tree.

    Args:
        directory_service: Service for directory operations
        project_id: Numeric project ID

    Returns:
        DirectoryNode tree containing only folders (type="directory")
    """
    structure = await directory_service.get_directory_structure()
    return structure


@router.get("/list", response_model=List[DirectoryNode], response_model_exclude_none=True)
async def list_directory(
    directory_service: DirectoryServiceV2Dep,
    project_id: ProjectIdPathDep,
    dir_name: str = Query("/", description="Directory path to list"),
    depth: int = Query(1, ge=1, le=10, description="Recursion depth (1-10)"),
    file_name_glob: Optional[str] = Query(
        None, description="Glob pattern for filtering file names"
    ),
):
    """List directory contents with filtering and depth control.

    Args:
        directory_service: Service for directory operations
        project_id: Numeric project ID
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
