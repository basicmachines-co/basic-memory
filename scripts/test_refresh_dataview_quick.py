#!/usr/bin/env python
"""Quick test script for refresh_dataview_relations() - tests only a few entities"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from basic_memory import db
from basic_memory.config import ConfigManager, get_project_config
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository import (
    EntityRepository,
    RelationRepository,
    ObservationRepository,
    ProjectRepository,
)
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.services import EntityService, FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync.sync_service import SyncService


async def main():
    print("=" * 80)
    print("Quick test: refresh_dataview_relations() on a few entities")
    print("=" * 80)
    
    # Get config for 'main' project
    config_manager = ConfigManager()
    app_config = config_manager.config
    project_config = get_project_config('main')
    
    print(f"\nProject: {project_config.name}")
    print(f"Path: {project_config.home}")
    print(f"Database: {app_config.database_path}")
    
    # Initialize database connection
    print("\n[1/6] Initializing database connection...")
    async with db.engine_session_factory(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM
    ) as (engine, session_maker):
        print("✓ Database connection established")
        
        # Get project from database
        print("\n[2/6] Loading project from database...")
        project_repository = ProjectRepository(session_maker)
        project = await project_repository.get_by_name('main')
        if not project:
            print("✗ Project 'main' not found in database")
            return
        print(f"✓ Project loaded: {project.name} (id={project.id})")
        
        # Initialize repositories
        print("\n[3/6] Initializing repositories...")
        entity_repository = EntityRepository(session_maker, project_id=project.id)
        relation_repository = RelationRepository(session_maker, project_id=project.id)
        observation_repository = ObservationRepository(session_maker, project_id=project.id)
        print("✓ Repositories initialized")
        
        # Initialize services
        print("\n[4/6] Initializing services...")
        entity_parser = EntityParser(project_config.home)
        markdown_processor = MarkdownProcessor(entity_parser)
        file_service = FileService(project_config.home, markdown_processor)
        
        # Search service
        search_repository = create_search_repository(
            session_maker,
            project_id=project.id,
            database_backend=app_config.database_backend
        )
        search_service = SearchService(search_repository, entity_repository, file_service)
        await search_service.init_search_index()
        
        # Link resolver and entity service
        link_resolver = LinkResolver(entity_repository, search_service)
        entity_service = EntityService(
            entity_parser=entity_parser,
            entity_repository=entity_repository,
            observation_repository=observation_repository,
            relation_repository=relation_repository,
            file_service=file_service,
            link_resolver=link_resolver,
            app_config=app_config,
        )
        print("✓ Services initialized")
        
        # Initialize sync service
        print("\n[5/6] Initializing sync service...")
        sync_service = SyncService(
            app_config=app_config,
            entity_service=entity_service,
            entity_parser=entity_parser,
            entity_repository=entity_repository,
            relation_repository=relation_repository,
            project_repository=project_repository,
            search_service=search_service,
            file_service=file_service,
        )
        print("✓ Sync service initialized")
        
        # Test on specific entities with Dataview queries
        print("\n[6/6] Testing refresh on specific entities...")
        test_entities = ["brio", "areas", "projects"]
        
        for permalink in test_entities:
            print(f"\n--- Testing entity: {permalink} ---")
            entity = await entity_repository.get_by_permalink(permalink)
            if not entity:
                print(f"✗ Entity '{permalink}' not found")
                continue
            
            print(f"✓ Found entity: {entity.title}")
            
            # Read file content
            file_content_tuple = await file_service.read_file(entity.file_path)
            if not file_content_tuple:
                print(f"✗ Could not read file: {entity.file_path}")
                continue
            
            file_content, _ = file_content_tuple  # Unpack tuple (content, checksum)
            
            # Count existing dataview_link relations for this entity
            all_relations = await relation_repository.find_all()
            existing = [r for r in all_relations if r.from_id == entity.id and r.relation_type == "dataview_link"]
            print(f"  Existing dataview_link relations: {len(existing)}")
            
            # Delete existing dataview_link relations for this entity
            for rel in existing:
                await relation_repository.delete(rel.id)
            print(f"  Deleted {len(existing)} existing relations")
            
            # Refresh dataview relations for this entity
            try:
                await sync_service._refresh_entity_dataview_relations(entity, file_content)
                print(f"  ✓ Refresh completed")
            except Exception as e:
                print(f"  ✗ Error during refresh: {e}")
                import traceback
                traceback.print_exc()
                continue
            
            # Count new dataview_link relations
            all_relations_after = await relation_repository.find_all()
            new_relations = [r for r in all_relations_after if r.from_id == entity.id and r.relation_type == "dataview_link"]
            print(f"  New dataview_link relations: {len(new_relations)}")
            
            # Show examples
            if new_relations:
                print(f"  Examples:")
                for i, rel in enumerate(new_relations[:3], 1):
                    to_entity = await entity_repository.find_by_id(rel.to_id) if rel.to_id else None
                    to_title = to_entity.title if to_entity else rel.to_name or "Unresolved"
                    print(f"    {i}. {entity.title} -> {to_title}")
        
        print("\n" + "=" * 80)
        print("Quick test completed!")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
