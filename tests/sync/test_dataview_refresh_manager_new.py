"""
New tests for DataviewRefreshManager - cache and FROM clause extraction features.
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from basic_memory.sync.dataview_refresh_manager import DataviewRefreshManager


@pytest.fixture
def mock_entity_repository():
    """Mock EntityRepository."""
    repo = Mock()
    repo.find_all = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_sync_service(mock_entity_repository):
    """Mock SyncService with refresh method."""
    service = Mock()
    service._refresh_entity_dataview_relations = AsyncMock()
    service.entity_repository = mock_entity_repository
    return service


@pytest.fixture
def manager(mock_sync_service):
    """Create DataviewRefreshManager instance."""
    return DataviewRefreshManager(
        sync_service=mock_sync_service,
        debounce_seconds=0.1
    )


class TestDataviewRefreshManagerCache:
    """Test cache functionality."""
    
    @pytest.mark.asyncio
    async def test_get_dataview_entities_caches_results(self, manager, mock_entity_repository):
        """Test that _get_dataview_entities caches results."""
        mock_entity1 = Mock()
        mock_entity1.id = 1
        mock_entity1.file_path = "milestone.md"
        mock_entity1.content = '```dataview\nFROM "product-memories"\n```'
        
        mock_entity2 = Mock()
        mock_entity2.id = 2
        mock_entity2.file_path = "regular.md"
        mock_entity2.content = "No dataview here"
        
        mock_entity_repository.find_all.return_value = [mock_entity1, mock_entity2]
        
        # First call
        result1 = await manager._get_dataview_entities()
        assert len(result1) == 1
        assert 1 in result1
        assert mock_entity_repository.find_all.call_count == 1
        
        # Second call should use cache
        result2 = await manager._get_dataview_entities()
        assert result2 == result1
        assert mock_entity_repository.find_all.call_count == 1  # Not called again
    
    @pytest.mark.asyncio
    async def test_invalidate_cache_clears_cache(self, manager, mock_entity_repository):
        """Test that invalidate_cache clears the cache."""
        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.file_path = "test.md"
        mock_entity.content = '```dataview\nFROM "test"\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity]
        
        # Build cache
        await manager._get_dataview_entities()
        assert manager._cache_valid
        
        # Invalidate
        manager.invalidate_cache()
        assert not manager._cache_valid
        assert manager._dataview_entities_cache is None
        
        # Next call should rebuild cache
        await manager._get_dataview_entities()
        assert mock_entity_repository.find_all.call_count == 2  # Called again


class TestDataviewRefreshManagerFromClauseExtraction:
    """Test FROM clause extraction from content."""
    
    @pytest.mark.asyncio
    async def test_extract_from_clauses_double_quotes(self, manager):
        """Test extraction of FROM clauses with double quotes."""
        content = '''
# Test Note

```dataview
TABLE status
FROM "product-memories"
WHERE type = "user-story"
```
        '''
        
        from_clauses = manager._extract_from_clauses(content)
        
        assert "product-memories" in from_clauses
        assert len(from_clauses) == 1
    
    @pytest.mark.asyncio
    async def test_extract_from_clauses_single_quotes(self, manager):
        """Test extraction of FROM clauses with single quotes."""
        content = '''
```dataview
LIST
FROM 'projects'
```
        '''
        
        from_clauses = manager._extract_from_clauses(content)
        
        assert "projects" in from_clauses
        assert len(from_clauses) == 1
    
    @pytest.mark.asyncio
    async def test_extract_multiple_from_clauses(self, manager):
        """Test extraction of multiple FROM clauses."""
        content = '''
```dataview
FROM "product-memories"
```

```dataview
FROM 'projects'
```

```dataview
FROM "areas"
```
        '''
        
        from_clauses = manager._extract_from_clauses(content)
        
        assert "product-memories" in from_clauses
        assert "projects" in from_clauses
        assert "areas" in from_clauses
        assert len(from_clauses) == 3
    
    @pytest.mark.asyncio
    async def test_extract_from_clauses_case_insensitive(self, manager):
        """Test that FROM extraction is case-insensitive."""
        content = '''
```dataview
from "test"
```

```dataview
From "test2"
```

```dataview
FROM "test3"
```
        '''
        
        from_clauses = manager._extract_from_clauses(content)
        
        assert "test" in from_clauses
        assert "test2" in from_clauses
        assert "test3" in from_clauses
        assert len(from_clauses) == 3


class TestDataviewRefreshManagerImpactDetection:
    """Test impact detection based on FROM clauses."""
    
    @pytest.mark.asyncio
    async def test_find_impacted_entities_folder_match(self, manager, mock_entity_repository):
        """Test finding entities impacted by folder changes."""
        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.file_path = "milestone.md"
        mock_entity.content = '```dataview\nFROM "product-memories"\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity]
        
        changes = {
            "product-memories/US-001.md": {
                "type": "user-story",
                "folder": "product-memories",
                "metadata": {}
            }
        }
        
        impacted = await manager._find_impacted_entities(changes)
        
        assert 1 in impacted
    
    @pytest.mark.asyncio
    async def test_find_impacted_entities_no_from_clause(self, manager, mock_entity_repository):
        """Test that entities without FROM clause are always impacted."""
        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.file_path = "dashboard.md"
        mock_entity.content = '```dataview\nTABLE status\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity]
        
        changes = {
            "anywhere/file.md": {
                "type": "note",
                "folder": "anywhere",
                "metadata": {}
            }
        }
        
        impacted = await manager._find_impacted_entities(changes)
        
        assert 1 in impacted
    
    @pytest.mark.asyncio
    async def test_find_impacted_entities_no_match(self, manager, mock_entity_repository):
        """Test that unrelated changes don't impact entities."""
        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.file_path = "milestone.md"
        mock_entity.content = '```dataview\nFROM "product-memories"\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity]
        
        changes = {
            "personal-notes/diary.md": {
                "type": "note",
                "folder": "personal-notes",
                "metadata": {}
            }
        }
        
        impacted = await manager._find_impacted_entities(changes)
        
        assert 1 not in impacted
        assert len(impacted) == 0


class TestDataviewRefreshManagerForceRefresh:
    """Test force refresh functionality."""
    
    @pytest.mark.asyncio
    async def test_force_refresh_all(self, manager, mock_entity_repository, mock_sync_service):
        """Test force_refresh_all refreshes all entities with Dataview."""
        mock_entity1 = Mock()
        mock_entity1.id = 1
        mock_entity1.file_path = "milestone.md"
        mock_entity1.content = '```dataview\nFROM "product-memories"\n```'
        
        mock_entity2 = Mock()
        mock_entity2.id = 2
        mock_entity2.file_path = "dashboard.md"
        mock_entity2.content = '```dataview\nTABLE status\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity1, mock_entity2]
        mock_entity_repository.find_by_id = AsyncMock(side_effect=lambda id: mock_entity1 if id == 1 else mock_entity2)
        
        # Mock file service
        mock_sync_service.file_service = Mock()
        mock_sync_service.file_service.read_file_content = AsyncMock(return_value="# Content")
        
        await manager.force_refresh_all()
        
        # Should refresh both entities
        assert mock_sync_service._refresh_entity_dataview_relations.call_count == 2
        called_entity_ids = {
            call[0][0].id for call in mock_sync_service._refresh_entity_dataview_relations.call_args_list
        }
        assert called_entity_ids == {1, 2}
    
    @pytest.mark.asyncio
    async def test_force_refresh_all_invalidates_cache(self, manager, mock_entity_repository):
        """Test that force_refresh_all invalidates cache first."""
        mock_entity = Mock()
        mock_entity.id = 1
        mock_entity.file_path = "test.md"
        mock_entity.content = '```dataview\nFROM "test"\n```'
        
        mock_entity_repository.find_all.return_value = [mock_entity]
        
        # Build cache
        await manager._get_dataview_entities()
        assert manager._cache_valid
        
        # Force refresh should invalidate cache
        await manager.force_refresh_all()
        
        # Cache should have been rebuilt
        assert mock_entity_repository.find_all.call_count == 2
