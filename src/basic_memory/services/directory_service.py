"""Directory service for managing file directories and tree structure."""

import fnmatch
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Sequence


from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.schemas.directory import DirectoryNode

logger = logging.getLogger(__name__)


def _mtime_to_datetime(entity: Entity) -> datetime:
    """Convert entity mtime (file modification time) to datetime.

    Returns the file's actual modification time, falling back to updated_at
    if mtime is not available.
    """
    if entity.mtime:
        return datetime.fromtimestamp(entity.mtime).astimezone()
    return entity.updated_at


class DirectoryService:
    """Service for working with directory trees."""

    def __init__(self, entity_repository: EntityRepository):
        """Initialize the directory service.

        Args:
            entity_repository: Directory repository for data access.
        """
        self.entity_repository = entity_repository

    async def get_directory_tree(self) -> DirectoryNode:
        """Build a hierarchical directory tree from indexed files."""

        # Get all files from DB (flat list)
        entity_rows = await self.entity_repository.find_all()

        # Create a root directory node
        root_node = DirectoryNode(name="Root", directory_path="/", type="directory")

        # Map to store directory nodes by path for easy lookup
        dir_map: Dict[str, DirectoryNode] = {root_node.directory_path: root_node}

        # First pass: create all directory nodes
        for file in entity_rows:
            # Process directory path components
            parts = [p for p in file.file_path.split("/") if p]

            # Create directory structure
            current_path = "/"
            for i, part in enumerate(parts[:-1]):  # Skip the filename
                parent_path = current_path
                # Build the directory path
                current_path = (
                    f"{current_path}{part}" if current_path == "/" else f"{current_path}/{part}"
                )

                # Create directory node if it doesn't exist
                if current_path not in dir_map:
                    dir_node = DirectoryNode(
                        name=part, directory_path=current_path, type="directory"
                    )
                    dir_map[current_path] = dir_node

                    # Add to parent's children
                    if parent_path in dir_map:
                        dir_map[parent_path].children.append(dir_node)

        # Second pass: add file nodes to their parent directories
        for file in entity_rows:
            file_name = os.path.basename(file.file_path)
            parent_dir = os.path.dirname(file.file_path)
            directory_path = "/" if parent_dir == "" else f"/{parent_dir}"

            # Create file node
            file_node = DirectoryNode(
                name=file_name,
                file_path=file.file_path,  # Original path from DB (no leading slash)
                directory_path=f"/{file.file_path}",  # Path with leading slash
                type="file",
                title=file.title,
                permalink=file.permalink,
                entity_id=file.id,
                entity_type=file.entity_type,
                content_type=file.content_type,
                updated_at=_mtime_to_datetime(file),
            )

            # Add to parent directory's children
            if directory_path in dir_map:
                dir_map[directory_path].children.append(file_node)
            else:
                # If parent directory doesn't exist (should be rare), add to root
                dir_map["/"].children.append(file_node)  # pragma: no cover

        # Return the root node with its children
        return root_node

    async def get_directory_structure(self) -> DirectoryNode:
        """Build a hierarchical directory structure without file details.

        Optimized method for folder navigation that only returns directory nodes,
        no file metadata. Much faster than get_directory_tree() for large knowledge bases.

        Returns:
            DirectoryNode tree containing only folders (type="directory")
        """
        # Get unique directories without loading entities
        directories = await self.entity_repository.get_distinct_directories()

        # Create a root directory node
        root_node = DirectoryNode(name="Root", directory_path="/", type="directory")

        # Map to store directory nodes by path for easy lookup
        dir_map: Dict[str, DirectoryNode] = {"/": root_node}

        # Build tree with just folders
        for dir_path in directories:
            parts = [p for p in dir_path.split("/") if p]
            current_path = "/"

            for i, part in enumerate(parts):
                parent_path = current_path
                # Build the directory path
                current_path = (
                    f"{current_path}{part}" if current_path == "/" else f"{current_path}/{part}"
                )

                # Create directory node if it doesn't exist
                if current_path not in dir_map:
                    dir_node = DirectoryNode(
                        name=part, directory_path=current_path, type="directory"
                    )
                    dir_map[current_path] = dir_node

                    # Add to parent's children
                    if parent_path in dir_map:
                        dir_map[parent_path].children.append(dir_node)

        return root_node

    async def list_directory(
        self,
        dir_name: str = "/",
        depth: int = 1,
        file_name_glob: Optional[str] = None,
    ) -> List[DirectoryNode]:
        """List directory contents with filtering and depth control.

        Args:
            dir_name: Directory path to list (default: root "/")
            depth: Recursion depth (1 = immediate children only)
            file_name_glob: Glob pattern for filtering file names

        Returns:
            List of DirectoryNode objects matching the criteria
        """
        # Normalize directory path
        # Strip ./ prefix if present (handles relative path notation)
        if dir_name.startswith("./"):
            dir_name = dir_name[2:]  # Remove "./" prefix

        # Ensure path starts with "/"
        if not dir_name.startswith("/"):
            dir_name = f"/{dir_name}"

        # Remove trailing slashes except for root
        if dir_name != "/" and dir_name.endswith("/"):
            dir_name = dir_name.rstrip("/")

        # Optimize: Query only entities in the target directory
        # instead of loading the entire tree
        dir_prefix = dir_name.lstrip("/")
        entity_rows = await self.entity_repository.find_by_directory_prefix(dir_prefix)

        # Build a partial tree from only the relevant entities
        root_tree = self._build_directory_tree_from_entities(entity_rows, dir_name)

        # Find the target directory node
        target_node = self._find_directory_node(root_tree, dir_name)
        if not target_node:
            return []

        # Collect nodes with depth and glob filtering
        result = []
        self._collect_nodes_recursive(target_node, result, depth, file_name_glob, 0)

        return result

    def _build_directory_tree_from_entities(
        self, entity_rows: Sequence[Entity], root_path: str
    ) -> DirectoryNode:
        """Build a directory tree from a subset of entities.

        Args:
            entity_rows: Sequence of entity objects to build tree from
            root_path: Root directory path for the tree

        Returns:
            DirectoryNode representing the tree root
        """
        # Create a root directory node
        root_node = DirectoryNode(name="Root", directory_path=root_path, type="directory")

        # Map to store directory nodes by path for easy lookup
        dir_map: Dict[str, DirectoryNode] = {root_path: root_node}

        # First pass: create all directory nodes
        for file in entity_rows:
            # Process directory path components
            parts = [p for p in file.file_path.split("/") if p]

            # Create directory structure
            current_path = "/"
            for i, part in enumerate(parts[:-1]):  # Skip the filename
                parent_path = current_path
                # Build the directory path
                current_path = (
                    f"{current_path}{part}" if current_path == "/" else f"{current_path}/{part}"
                )

                # Create directory node if it doesn't exist
                if current_path not in dir_map:
                    dir_node = DirectoryNode(
                        name=part, directory_path=current_path, type="directory"
                    )
                    dir_map[current_path] = dir_node

                    # Add to parent's children
                    if parent_path in dir_map:
                        dir_map[parent_path].children.append(dir_node)

        # Second pass: add file nodes to their parent directories
        for file in entity_rows:
            file_name = os.path.basename(file.file_path)
            parent_dir = os.path.dirname(file.file_path)
            directory_path = "/" if parent_dir == "" else f"/{parent_dir}"

            # Create file node
            file_node = DirectoryNode(
                name=file_name,
                file_path=file.file_path,
                directory_path=f"/{file.file_path}",
                type="file",
                title=file.title,
                permalink=file.permalink,
                entity_id=file.id,
                entity_type=file.entity_type,
                content_type=file.content_type,
                updated_at=_mtime_to_datetime(file),
            )

            # Add to parent directory's children
            if directory_path in dir_map:
                dir_map[directory_path].children.append(file_node)
            elif root_path in dir_map:
                # Fallback to root if parent not found
                dir_map[root_path].children.append(file_node)

        return root_node

    def _find_directory_node(
        self, root: DirectoryNode, target_path: str
    ) -> Optional[DirectoryNode]:
        """Find a directory node by path in the tree."""
        if root.directory_path == target_path:
            return root

        for child in root.children:
            if child.type == "directory":
                found = self._find_directory_node(child, target_path)
                if found:
                    return found

        return None

    def _collect_nodes_recursive(
        self,
        node: DirectoryNode,
        result: List[DirectoryNode],
        max_depth: int,
        file_name_glob: Optional[str],
        current_depth: int,
    ) -> None:
        """Recursively collect nodes with depth and glob filtering."""
        if current_depth >= max_depth:
            return

        for child in node.children:
            # Apply glob filtering
            if file_name_glob and not fnmatch.fnmatch(child.name, file_name_glob):
                continue

            # Add the child to results
            result.append(child)

            # Recurse into subdirectories if we haven't reached max depth
            if child.type == "directory" and current_depth < max_depth:
                self._collect_nodes_recursive(
                    child, result, max_depth, file_name_glob, current_depth + 1
                )
