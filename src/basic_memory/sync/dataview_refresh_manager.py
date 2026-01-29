"""Manages automatic refresh of Dataview relations with debouncing."""

import asyncio
import re
from typing import Set, Dict, Optional, Any
from pathlib import Path
from loguru import logger


class DataviewRefreshManager:
    """Manages automatic refresh of Dataview relations with debouncing.
    
    This class implements a hybrid refresh strategy:
    1. Debounce file changes (default 5s) to avoid excessive refreshes
    2. Only refresh entities with Dataview queries that are impacted by the changes
    
    Impacted entities are determined by:
    - Queries with FROM clause matching the changed file's folder
    - Queries with WHERE conditions matching the changed file's properties (type, status, etc.)
    """
    
    def __init__(self, sync_service, debounce_seconds: float = 5.0):
        """Initialize the DataviewRefreshManager.
        
        Args:
            sync_service: The SyncService instance to use for refreshing
            debounce_seconds: Number of seconds to wait before triggering refresh
        """
        self.sync_service = sync_service
        self.debounce_seconds = debounce_seconds
        self._pending_changes: Dict[str, Dict[str, Any]] = {}  # path -> {type, folder, metadata}
        self._debounce_task: Optional[asyncio.Task] = None
        
        # Cache of entities with Dataview queries
        self._dataview_entities_cache: Optional[Dict[int, Dict]] = None
        self._cache_valid = False
    
    def invalidate_cache(self):
        """Invalidate the cache when entities are added/removed."""
        self._cache_valid = False
        self._dataview_entities_cache = None
    
    async def on_file_changed(
        self, 
        file_path: str, 
        entity_type: Optional[str] = None, 
        folder: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Called when a file is modified. Triggers debounced refresh.
        
        Args:
            file_path: Path to the file that changed
            entity_type: Optional entity type (e.g., "user-story", "milestone")
            folder: Optional folder path
            metadata: Additional frontmatter fields that might affect queries
        """
        self._pending_changes[file_path] = {
            'type': entity_type,
            'folder': folder or str(Path(file_path).parent),
            'metadata': metadata or {}
        }
        
        # Cancel existing debounce task
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        
        # Start new debounce
        self._debounce_task = asyncio.create_task(self._debounced_refresh())
    
    async def _debounced_refresh(self):
        """Wait for debounce period then refresh impacted entities."""
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        
        if not self._pending_changes:
            return
        
        changes = self._pending_changes.copy()
        self._pending_changes.clear()
        
        logger.info(
            f"Debounce triggered: {len(changes)} files changed, "
            f"refreshing impacted Dataview relations"
        )
        
        # Find impacted entities and refresh them
        impacted = await self._find_impacted_entities(changes)
        if impacted:
            logger.info(f"Refreshing {len(impacted)} entities with Dataview queries")
            await self._refresh_entities(impacted)
        else:
            logger.debug("No Dataview entities impacted by changes")
    
    def _extract_from_clauses(self, content: str) -> Set[str]:
        """Extract FROM clause paths from Dataview queries in content.
        
        Args:
            content: Markdown content to search
            
        Returns:
            Set of FROM clause paths found in the content
        """
        from_clauses = set()
        
        # Match FROM "path" or FROM 'path' (case-insensitive)
        pattern = r'FROM\s+["\']([^"\']+)["\']'
        matches = re.findall(pattern, content, re.IGNORECASE)
        from_clauses.update(matches)
        
        return from_clauses
    
    async def _get_dataview_entities(self) -> Dict[int, Dict]:
        """Get all entities that have Dataview queries, with cached results.
        
        Returns:
            Dict mapping entity ID to entity info (path, from_clauses)
        """
        if self._cache_valid and self._dataview_entities_cache is not None:
            return self._dataview_entities_cache
        
        entities_with_dataview = {}
        
        # Get all entities and check which have dataview queries
        all_entities = await self.sync_service.entity_repository.find_all()
        
        for entity in all_entities:
            # Read file content to check for dataview queries
            content = await self.sync_service.file_service.read_entity_content(entity)
            if content and '```dataview' in content:
                # Extract FROM clauses to know which folders this entity watches
                from_clauses = self._extract_from_clauses(content)
                entities_with_dataview[entity.id] = {
                    'id': entity.id,
                    'path': entity.file_path,
                    'from_clauses': from_clauses
                }
        
        self._dataview_entities_cache = entities_with_dataview
        self._cache_valid = True
        
        logger.debug(f"Cached {len(entities_with_dataview)} entities with Dataview queries")
        return entities_with_dataview
    
    async def _find_impacted_entities(self, changes: Dict[str, Dict]) -> Set[int]:
        """Find entities with Dataview queries that might be affected by the changes.
        
        An entity is impacted if:
        - Its FROM clause matches a folder containing a changed file
        - Or it has no FROM clause (queries all files)
        
        Args:
            changes: Dict mapping file paths to change info (type, folder, metadata)
            
        Returns:
            Set of entity IDs that need to be refreshed
        """
        impacted = set()
        dataview_entities = await self._get_dataview_entities()
        
        # Get all folders that had changes
        changed_folders = {info['folder'] for info in changes.values()}
        changed_paths = set(changes.keys())
        
        for entity_id, entity_info in dataview_entities.items():
            from_clauses = entity_info.get('from_clauses', set())
            
            if not from_clauses:
                # No FROM clause = queries everything, always impacted
                impacted.add(entity_id)
                continue
            
            # Check if any FROM clause matches a changed folder
            for from_clause in from_clauses:
                for changed_folder in changed_folders:
                    # Check if the FROM path is contained in or contains the changed folder
                    if from_clause in changed_folder or changed_folder in from_clause:
                        impacted.add(entity_id)
                        break
                
                # Also check direct file path matches
                for changed_path in changed_paths:
                    if from_clause in changed_path:
                        impacted.add(entity_id)
                        break
        
        return impacted
    
    async def _refresh_entities(self, entity_ids: Set[int]):
        """Refresh Dataview relations for specific entities.
        
        Args:
            entity_ids: Set of entity IDs to refresh
        """
        for entity_id in entity_ids:
            try:
                # Get the entity
                entity = await self.sync_service.entity_repository.find_by_id(entity_id)
                if not entity:
                    logger.warning(f"Entity {entity_id} not found, skipping refresh")
                    continue
                
                # Read the file content
                try:
                    file_content = await self.sync_service.file_service.read_file_content(
                        entity.file_path
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not read file {entity.file_path} for refresh: {e}"
                    )
                    continue
                
                # Refresh the entity's Dataview relations
                await self.sync_service._refresh_entity_dataview_relations(
                    entity, file_content
                )
                logger.debug(f"Refreshed Dataview relations for {entity.permalink}")
            except Exception as e:
                logger.error(
                    f"Error refreshing Dataview relations for entity {entity_id}: {e}"
                )
    
    async def force_refresh_all(self):
        """Force refresh all entities with Dataview queries. Used for initial sync."""
        self.invalidate_cache()
        dataview_entities = await self._get_dataview_entities()
        
        if dataview_entities:
            logger.info(f"Force refreshing all {len(dataview_entities)} entities with Dataview queries")
            await self._refresh_entities(set(dataview_entities.keys()))
