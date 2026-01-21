#!/usr/bin/env python
"""Test script for refresh_dataview_relations()

This script properly initializes all services and tests the Dataview refresh functionality.
"""
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
    print("Testing refresh_dataview_relations()")
    print("=" * 80)
    
    # Get config for 'main' project
    config_manager = ConfigManager()
    app_config = config_manager.config
    project_config = get_project_config('main')
    
    print(f"\nProject: {project_config.name}")
    print(f"Path: {project_config.home}")
    print(f"Database: {app_config.database_path}")
    
    # Initialize database connection
    print("\n[1/8] Initializing database connection...")
    async with db.engine_session_factory(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM
    ) as (engine, session_maker):
        print("✓ Database connection established")
        
        # Get project from database
        print("\n[2/8] Loading project from database...")
        project_repository = ProjectRepository(session_maker)
        project = await project_repository.get_by_name('main')
        if not project:
            print("✗ Project 'main' not found in database")
            return
        print(f"✓ Project loaded: {project.name} (id={project.id})")
        
        # Initialize repositories
        print("\n[3/8] Initializing repositories...")
        entity_repository = EntityRepository(session_maker, project_id=project.id)
        relation_repository = RelationRepository(session_maker, project_id=project.id)
        observation_repository = ObservationRepository(session_maker, project_id=project.id)
        print("✓ Repositories initialized")
        
        # Initialize services
        print("\n[4/8] Initializing services...")
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
        print("\n[5/8] Initializing sync service...")
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
        
        # Count existing dataview_link relations before refresh
        print("\n[6/8] Counting existing dataview_link relations...")
        all_relations = await relation_repository.find_all()
        dataview_relations_before = [r for r in all_relations if r.relation_type == "dataview_link"]
        print(f"✓ Found {len(dataview_relations_before)} existing dataview_link relations")
        
        # Call refresh_dataview_relations
        print("\n[7/8] Calling refresh_dataview_relations()...")
        try:
            await sync_service.refresh_dataview_relations()
            print("✓ refresh_dataview_relations() completed successfully")
        except Exception as e:
            print(f"✗ Error during refresh: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Count dataview_link relations after refresh
        print("\n[8/8] Verifying results...")
        all_relations_after = await relation_repository.find_all()
        dataview_relations_after = [r for r in all_relations_after if r.relation_type == "dataview_link"]
        print(f"✓ Found {len(dataview_relations_after)} dataview_link relations after refresh")
        
        # Show some examples
        if dataview_relations_after:
            print("\nExample dataview_link relations:")
            for i, rel in enumerate(dataview_relations_after[:5], 1):
                from_entity = await entity_repository.find_by_id(rel.from_id)
                to_entity = await entity_repository.find_by_id(rel.to_id) if rel.to_id else None
                from_title = from_entity.title if from_entity else "Unknown"
                to_title = to_entity.title if to_entity else rel.to_name or "Unresolved"
                print(f"  {i}. {from_title} -> {to_title}")
        else:
            print("\nNo dataview_link relations found.")
            print("This could mean:")
            print("  - No notes have Dataview queries")
            print("  - Dataview queries returned no results")
            print("  - There was an error during processing")
        
        print("\n" + "=" * 80)
        print("Test completed!")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
