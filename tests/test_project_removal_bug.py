#!/usr/bin/env python3
"""
Test script to reproduce and verify the fix for project removal bug.

This script creates a project with related entities and attempts to remove it,
demonstrating the foreign key constraint issue and its resolution.
"""

import asyncio
import os
import tempfile
import sys
from pathlib import Path

# Add src to path so we can import basic_memory modules
sys.path.insert(0, "src")

from basic_memory.models import Project, Entity
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.services.project_service import ProjectService
from basic_memory.db import get_or_create_db, DatabaseType, scoped_session
from sqlalchemy import text


async def test_project_removal_bug():
    """Test the project removal bug reproduction and fix."""
    print("🔍 Testing project removal with foreign key constraints...")
    
    # Create in-memory database for testing
    db_path = Path(":memory:")
    engine, session_maker = await get_or_create_db(db_path, DatabaseType.MEMORY)
    
    # Create repositories
    project_repo = ProjectRepository(session_maker)
    entity_repo = EntityRepository(session_maker, project_id=None)
    
    # Create project service
    project_service = ProjectService(project_repo)
    
    try:
        # Step 1: Create a test project
        print("📁 Creating test project...")
        test_project_name = "test-project-with-entities"
        test_project_path = "/tmp/test-project"
        
        await project_service.add_project(test_project_name, test_project_path)
        project = await project_service.get_project(test_project_name)
        assert project is not None, "Project should have been created"
        print(f"✅ Created project: {project.name} (ID: {project.id})")
        
        # Step 2: Create related entities
        print("📄 Creating related entities...")
        entity_data = {
            "title": "Test Entity",
            "entity_type": "note",
            "content_type": "text/markdown",
            "project_id": project.id,
            "permalink": "test-entity",
            "file_path": "test-entity.md",
            "checksum": "abc123"
        }
        entity = await entity_repo.create(entity_data)
        print(f"✅ Created entity: {entity.title} (ID: {entity.id})")
        
        # Step 3: Verify foreign key constraint exists
        print("🔗 Checking foreign key constraints...")
        async with scoped_session(session_maker) as session:
            # Get foreign key info for entity table
            result = await session.execute(text("PRAGMA foreign_key_list(entity)"))
            fk_info = result.fetchall()
            print(f"Foreign keys on entity table: {fk_info}")
            
            if not fk_info:
                print("⚠️  WARNING: No foreign key constraints found!")
            else:
                for fk in fk_info:
                    print(f"  - FK: {fk}")
        
        # Step 4: Attempt to remove the project (this should trigger the bug)
        print("🗑️  Attempting to remove project with related entities...")
        try:
            await project_service.remove_project(test_project_name)
            print("✅ Project removal succeeded!")
            
            # Verify project is gone
            removed_project = await project_service.get_project(test_project_name)
            assert removed_project is None, "Project should have been removed"
            
            # Verify related entities are also gone (cascade delete)
            remaining_entity = await entity_repo.find_by_id(entity.id)
            if remaining_entity is None:
                print("✅ Related entities were properly cascade deleted")
            else:
                print("⚠️  Related entities still exist (cascade delete may not be working)")
                
        except Exception as e:
            print(f"❌ Project removal failed with error: {e}")
            print(f"   Error type: {type(e).__name__}")
            
            # Check if it's the specific foreign key constraint error
            if "FOREIGN KEY constraint failed" in str(e):
                print("🎯 This is the expected bug - foreign key constraint failure!")
                print("   The foreign key relationship is missing or misconfigured.")
                return False
            else:
                raise e
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed with unexpected error: {e}")
        raise e


async def main():
    """Main test function."""
    print("🧪 Project Removal Bug Test")
    print("=" * 50)
    
    try:
        success = await test_project_removal_bug()
        if success:
            print("\n✅ Test PASSED - Project removal worked correctly")
            return 0
        else:
            print("\n❌ Test FAILED - Project removal bug reproduced")
            return 1
    except Exception as e:
        print(f"\n💥 Test ERROR - Unexpected failure: {e}")
        return 2


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)