"""Directory service for managing file directories and tree structure."""

import fnmatch
import logging
import os
from typing import Dict, List, Optional

from basic_memory.repository import EntityRepository
from basic_memory.schemas.directory import DirectoryNode

logger = logging.getLogger(__name__)


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
                updated_at=file.updated_at,
            )

            # Add to parent directory's children
            if directory_path in dir_map:
                dir_map[directory_path].children.append(file_node)
            else:
                # If parent directory doesn't exist (should be rare), add to root
                dir_map["/"].children.append(file_node)  # pragma: no cover

        # Return the root node with its children
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
        if not dir_name.startswith("/"):
            dir_name = f"/{dir_name}"
        if dir_name != "/" and dir_name.endswith("/"):
            dir_name = dir_name.rstrip("/")

        # Get the full directory tree
        root_tree = await self.get_directory_tree()

        # Find the target directory node
        target_node = self._find_directory_node(root_tree, dir_name)
        if not target_node:
            return []

        # Collect nodes with depth and glob filtering
        result = []
        self._collect_nodes_recursive(target_node, result, depth, file_name_glob, 0)

        return result

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
