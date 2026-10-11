"""V2 Directory Router - ID-based directory tree operations.

This router provides directory structure browsing for projects using
external_id UUIDs instead of name-based identifiers.

Key improvements:
- Direct project lookup via external_id UUIDs
- Consistent with other v2 endpoints
- Better performance through indexed queries
"""

from fastapi import APIRouter, Path, Query

from basic_memory.read_cache import ReadCacheOperation
from basic_memory.read_cache.policy import READ_CACHE_TTL_SECONDS
from basic_memory.read_cache.routes import cached_read, content_group, project_group
from basic_memory.deps import DirectoryServiceV2ExternalDep
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
    DirectoryListResponse,
    DirectoryNode,
    DirectorySortOrder,
)

router = APIRouter(prefix="/directory", tags=["directory-v2"])


@router.get(
    "/tree",
    response_model=DirectoryNode,
    response_model_exclude_none=True,
    # File nodes carry titles and modified times, which body edits change.
    dependencies=[
        cached_read(
            ReadCacheOperation.directory_tree,
            group=content_group,
            ttl_seconds=READ_CACHE_TTL_SECONDS,
        )
    ],
)
async def get_directory_tree(
    directory_service: DirectoryServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
) -> DirectoryNode:
    """Get hierarchical directory structure from the knowledge base.

    Args:
        directory_service: Service for directory operations
        project_id: Project external UUID

    Returns:
        DirectoryNode representing the root of the hierarchical tree structure
    """
    return await directory_service.get_directory_tree()


@router.get(
    "/structure",
    response_model=DirectoryNode,
    response_model_exclude_none=True,
    # Folders only: changes when notes are created, moved or deleted.
    dependencies=[
        cached_read(
            ReadCacheOperation.directory_structure,
            group=project_group,
            ttl_seconds=READ_CACHE_TTL_SECONDS,
        )
    ],
)
async def get_directory_structure(
    directory_service: DirectoryServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
) -> DirectoryNode:
    """Get folder structure for navigation (no files).

    Optimized endpoint for folder tree navigation. Returns only directory nodes
    without file metadata. For full tree with files, use /directory/tree.

    Args:
        directory_service: Service for directory operations
        project_id: Project external UUID

    Returns:
        DirectoryNode tree containing only folders (type="directory")
    """
    return await directory_service.get_directory_structure()


@router.get(
    "/list",
    response_model=DirectoryListResponse,
    response_model_exclude_none=True,
    dependencies=[
        cached_read(
            ReadCacheOperation.directory_list,
            group=content_group,
            ttl_seconds=READ_CACHE_TTL_SECONDS,
        )
    ],
)
async def list_directory(
    directory_service: DirectoryServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    dir_name: str = Query("/", description="Directory path to list"),
    depth: int = Query(1, ge=1, le=10, description="Recursion depth (1-10)"),
    file_name_glob: str | None = Query(None, description="Glob pattern for filtering file names"),
    sort: DirectorySortOrder | None = Query(
        None,
        description=(
            "Optional file ordering. Directories are always returned first; omitting sort "
            "preserves legacy filename ordering."
        ),
    ),
    page: int = Query(1, ge=1, description="One-indexed result page"),
    page_size: int = Query(
        DEFAULT_DIRECTORY_PAGE_SIZE,
        ge=1,
        le=MAX_DIRECTORY_PAGE_SIZE,
        description="Number of nodes per page",
    ),
) -> DirectoryListResponse:
    """List directory contents with filtering and depth control.

    Args:
        directory_service: Service for directory operations
        project_id: Project external UUID
        dir_name: Directory path to list (default: root "/")
        depth: Recursion depth (1-10, default: 1 for immediate children only)
        file_name_glob: Optional glob pattern for filtering file names (e.g., "*.md", "*meeting*")
        sort: Optional title or updated-time ordering for files
        page: One-indexed result page
        page_size: Number of nodes per page (1-200)

    Returns:
        Bounded page of DirectoryNode objects matching the criteria
    """
    return await directory_service.list_directory(
        dir_name=dir_name,
        depth=depth,
        file_name_glob=file_name_glob,
        sort=sort,
        page=page,
        page_size=page_size,
    )
