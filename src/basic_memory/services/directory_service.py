"""Directory service for managing file directories and tree structure."""

import logging
import os
from typing import Dict

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
