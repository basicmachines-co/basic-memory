"""V2 Directory Router - ID-based directory tree operations.

This router provides directory structure browsing for projects using
external_id UUIDs instead of name-based identifiers.

Key improvements:
- Direct project lookup via external_id UUIDs
- Consistent with other v2 endpoints
- Better performance through indexed queries
"""

from contextlib import nullcontext
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from basic_memory.deps import (
    create_model_read_cache,
    DirectoryServiceV2ExternalDep,
    ReadCacheDep,
)
from basic_memory.read_cache import (
    ModelReadCache,
    ReadCacheKey,
    ReadCacheOperation,
    ReadCacheScope,
    read_cache_request_digest,
)
from basic_memory.read_cache.policy import DIRECTORY_READ_CACHE_MAX_PAYLOAD_BYTES
from basic_memory.schemas.directory import (
    DEFAULT_DIRECTORY_PAGE_SIZE,
    MAX_DIRECTORY_PAGE_SIZE,
    DirectoryListResponse,
    DirectoryNode,
)

router = APIRouter(prefix="/directory", tags=["directory-v2"])


def get_directory_node_read_cache(
    read_cache: ReadCacheDep,
) -> ModelReadCache[DirectoryNode] | None:
    """Bind directory trees and structures to the optional cache backend."""
    return create_model_read_cache(
        read_cache,
        DirectoryNode,
        max_payload_bytes=DIRECTORY_READ_CACHE_MAX_PAYLOAD_BYTES,
    )


DirectoryNodeReadCacheDep = Annotated[
    ModelReadCache[DirectoryNode] | None,
    Depends(get_directory_node_read_cache),
]


def get_directory_list_read_cache(
    read_cache: ReadCacheDep,
) -> ModelReadCache[DirectoryListResponse] | None:
    """Bind paginated directory listings to the optional cache backend."""
    return create_model_read_cache(read_cache, DirectoryListResponse)


DirectoryListReadCacheDep = Annotated[
    ModelReadCache[DirectoryListResponse] | None,
    Depends(get_directory_list_read_cache),
]


@router.get("/tree", response_model=DirectoryNode, response_model_exclude_none=True)
async def get_directory_tree(
    directory_service: DirectoryServiceV2ExternalDep,
    read_cache: DirectoryNodeReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
) -> DirectoryNode:
    """Get hierarchical directory structure from the knowledge base.

    Args:
        directory_service: Service for directory operations
        project_id: Project external UUID

    Returns:
        DirectoryNode representing the root of the hierarchical tree structure
    """
    cache_key = ReadCacheKey(
        project_id=project_id,
        operation=ReadCacheOperation.directory_tree,
        request_digest=read_cache_request_digest("tree"),
    )
    cache_scope = (
        read_cache.read(key=cache_key)
        if read_cache is not None
        else nullcontext(ReadCacheScope[DirectoryNode]())
    )
    async with cache_scope as cached:
        if cached.value is not None:
            return cached.value

        result = await directory_service.get_directory_tree()
        cached.value = result
        return result


@router.get("/structure", response_model=DirectoryNode, response_model_exclude_none=True)
async def get_directory_structure(
    directory_service: DirectoryServiceV2ExternalDep,
    read_cache: DirectoryNodeReadCacheDep,
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
    cache_key = ReadCacheKey(
        project_id=project_id,
        operation=ReadCacheOperation.directory_structure,
        request_digest=read_cache_request_digest("structure"),
    )
    cache_scope = (
        read_cache.read(key=cache_key)
        if read_cache is not None
        else nullcontext(ReadCacheScope[DirectoryNode]())
    )
    async with cache_scope as cached:
        if cached.value is not None:
            return cached.value

        result = await directory_service.get_directory_structure()
        cached.value = result
        return result


@router.get(
    "/list",
    response_model=DirectoryListResponse,
    response_model_exclude_none=True,
)
async def list_directory(
    directory_service: DirectoryServiceV2ExternalDep,
    read_cache: DirectoryListReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
    dir_name: str = Query("/", description="Directory path to list"),
    depth: int = Query(1, ge=1, le=10, description="Recursion depth (1-10)"),
    file_name_glob: str | None = Query(None, description="Glob pattern for filtering file names"),
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
        page: One-indexed result page
        page_size: Number of nodes per page (1-200)

    Returns:
        Bounded page of DirectoryNode objects matching the criteria
    """
    cache_key = ReadCacheKey(
        project_id=project_id,
        operation=ReadCacheOperation.directory_list,
        request_digest=read_cache_request_digest(
            dir_name,
            str(depth),
            file_name_glob or "",
            str(page),
            str(page_size),
        ),
    )
    cache_scope = (
        read_cache.read(key=cache_key)
        if read_cache is not None
        else nullcontext(ReadCacheScope[DirectoryListResponse]())
    )
    async with cache_scope as cached:
        if cached.value is not None:
            return cached.value

        result = await directory_service.list_directory(
            dir_name=dir_name,
            depth=depth,
            file_name_glob=file_name_glob,
            page=page,
            page_size=page_size,
        )
        cached.value = result
        return result
