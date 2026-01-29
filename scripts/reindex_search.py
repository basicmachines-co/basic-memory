#!/usr/bin/env python3
"""Rebuild the search_index FTS5 table without dropping entity data.

Usage:
    cd ~/Developer/basic-memory
    uv run python scripts/reindex_search.py [--project main]
"""

import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from basic_memory import db
from basic_memory.config import ConfigManager
from basic_memory.repository import ProjectRepository, EntityRepository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService


async def reindex_project(project_name: str = "main"):
    """Rebuild search index for a specific project."""

    print(f"🔄 Rebuilding search index for project: {project_name}")

    # Get config
    app_config = ConfigManager().config

    # Get database session
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )

    # Find project
    project_repo = ProjectRepository(session_maker)
    project = await project_repo.get_by_name(project_name)

    if not project:
        print(f"❌ Project '{project_name}' not found")
        await db.shutdown_db()
        return False

    print(f"   Project path: {project.path}")
    print(f"   Project ID: {project.id}")

    # Create dependencies
    project_path = Path(project.path)
    entity_parser = EntityParser(project_path)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(project_path, markdown_processor, app_config=app_config)

    # Create repositories
    entity_repository = EntityRepository(session_maker, project_id=project.id)
    search_repository = SQLiteSearchRepository(session_maker, project_id=project.id)

    # Create search service
    search_service = SearchService(search_repository, entity_repository, file_service)

    # Count entities before
    entities = await entity_repository.find_all()
    print(f"   Found {len(entities)} entities to index")

    # Reindex
    print("   Reindexing...")
    await search_service.reindex_all()

    # Verify
    from sqlalchemy import text
    result = await search_repository.execute_query(
        text("SELECT COUNT(*) as count FROM search_index WHERE project_id = :project_id"),
        params={"project_id": project.id}
    )
    row = result.fetchone()
    count = row[0] if row else 0

    print(f"✅ Done! search_index now has {count} entries for project '{project_name}'")

    # Cleanup
    await db.shutdown_db()
    return True


async def main():
    project_name = sys.argv[1] if len(sys.argv) > 1 else "main"

    # Remove --project prefix if present
    if project_name == "--project" and len(sys.argv) > 2:
        project_name = sys.argv[2]

    success = await reindex_project(project_name)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
