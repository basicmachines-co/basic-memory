#!/usr/bin/env python3
"""
Test script to verify cascade delete behavior on production SQLite database.

This script tests whether foreign key constraints with CASCADE DELETE are properly
configured in the production database at ~/.basic-memory/memory.db.

Usage: python test_production_cascade_delete.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class ProductionCascadeTest:
    """Test cascade delete behavior on production database."""

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize test with database path."""
        if db_path is None:
            # Default to standard Basic Memory location
            home_dir = Path.home()
            self.db_path = home_dir / ".basic-memory" / "memory.db"
        else:
            self.db_path = db_path

        # Create backup path
        self.backup_path = self.db_path.with_suffix(".db.backup")

        self.engine = None
        self.session_maker = None

    async def setup(self):
        """Setup database connection."""
        if not self.db_path.exists():
            print(f"❌ Production database not found at: {self.db_path}")
            print("Please ensure Basic Memory has been initialized and the database exists.")
            sys.exit(1)

        print(f"📁 Using database: {self.db_path}")

        # Create backup
        print(f"💾 Creating backup: {self.backup_path}")
        import shutil

        shutil.copy2(self.db_path, self.backup_path)

        # Connect to database
        db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def cleanup(self):
        """Cleanup database connection."""
        if self.engine:
            await self.engine.dispose()

    async def check_foreign_keys_enabled(self) -> bool:
        """Check if foreign keys are enabled in this session."""
        async with self.session_maker() as session:
            # Enable foreign keys like production does
            await session.execute(text("PRAGMA foreign_keys=ON"))

            result = await session.execute(text("PRAGMA foreign_keys"))
            fk_enabled = result.fetchone()[0]
            return bool(fk_enabled)

    async def check_schema(self):
        """Check current database schema for foreign key constraints."""
        async with self.session_maker() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))

            # Check entity table foreign keys
            result = await session.execute(text("PRAGMA foreign_key_list(entity)"))
            entity_fks = result.fetchall()

            print("🔍 Current entity table foreign key constraints:")
            for fk in entity_fks:
                print(f"  - Column: {fk[3]} -> {fk[2]}.{fk[4]} (ON DELETE: {fk[6]})")

            # Check if CASCADE DELETE is configured
            has_cascade = any(fk[6] == "CASCADE" for fk in entity_fks)

            if has_cascade:
                print("✅ CASCADE DELETE is configured")
            else:
                print("❌ CASCADE DELETE is NOT configured (uses NO ACTION)")

            return has_cascade

    async def create_test_data(self) -> tuple[int, int]:
        """Create test project and entity. Returns (project_id, entity_id)."""
        async with self.session_maker() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))

            # Create test project
            project_sql = """
            INSERT INTO project (name, description, permalink, path, is_active, is_default, created_at, updated_at)
            VALUES (:name, :description, :permalink, :path, :is_active, :is_default, :created_at, :updated_at)
            """
            now = datetime.now(timezone.utc)

            result = await session.execute(
                text(project_sql),
                {
                    "name": "cascade-test-project",
                    "description": "Test project for cascade delete verification",
                    "permalink": "cascade-test-project",
                    "path": "/tmp/cascade-test",
                    "is_active": True,
                    "is_default": False,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            project_id = result.lastrowid

            # Create test entity linked to project
            entity_sql = """
            INSERT INTO entity (title, entity_type, content_type, project_id, permalink, file_path, 
                              checksum, created_at, updated_at)
            VALUES (:title, :entity_type, :content_type, :project_id, :permalink, :file_path, 
                    :checksum, :created_at, :updated_at)
            """

            result = await session.execute(
                text(entity_sql),
                {
                    "title": "Cascade Test Entity",
                    "entity_type": "note",
                    "content_type": "text/markdown",
                    "project_id": project_id,
                    "permalink": "cascade-test-entity",
                    "file_path": "cascade-test-entity.md",
                    "checksum": "test-checksum",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            entity_id = result.lastrowid

            await session.commit()

            print(f"📝 Created test project (ID: {project_id}) and entity (ID: {entity_id})")
            return project_id, entity_id

    async def verify_test_data_exists(self, project_id: int, entity_id: int) -> bool:
        """Verify test data exists before deletion."""
        async with self.session_maker() as session:
            # Check project exists
            result = await session.execute(
                text("SELECT COUNT(*) FROM project WHERE id = :project_id"),
                {"project_id": project_id},
            )
            project_count = result.fetchone()[0]

            # Check entity exists
            result = await session.execute(
                text("SELECT COUNT(*) FROM entity WHERE id = :entity_id"), {"entity_id": entity_id}
            )
            entity_count = result.fetchone()[0]

            exists = project_count > 0 and entity_count > 0
            if exists:
                print(
                    f"✅ Test data verified: project ({project_count}) and entity ({entity_count}) exist"
                )
            else:
                print(
                    f"❌ Test data missing: project ({project_count}) and entity ({entity_count})"
                )

            return exists

    async def test_cascade_delete(self, project_id: int, entity_id: int) -> bool:
        """Test if deleting project cascades to delete entity."""
        async with self.session_maker() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))

            try:
                # Attempt to delete project
                print(f"🗑️  Attempting to delete project (ID: {project_id})...")

                result = await session.execute(
                    text("DELETE FROM project WHERE id = :project_id"), {"project_id": project_id}
                )

                if result.rowcount == 0:
                    print("❌ Project deletion failed - no rows affected")
                    return False

                await session.commit()
                print("✅ Project deletion succeeded")

                # Check if entity was cascade deleted
                result = await session.execute(
                    text("SELECT COUNT(*) FROM entity WHERE id = :entity_id"),
                    {"entity_id": entity_id},
                )
                entity_count = result.fetchone()[0]

                if entity_count == 0:
                    print("✅ CASCADE DELETE working: Entity was automatically deleted")
                    return True
                else:
                    print(
                        "❌ CASCADE DELETE NOT working: Entity still exists after project deletion"
                    )
                    return False

            except Exception as e:
                await session.rollback()
                print(f"❌ Project deletion failed with error: {e}")

                # Check if it's a foreign key constraint error
                if "FOREIGN KEY constraint failed" in str(e):
                    print(
                        "🔍 This confirms foreign key constraints are enforced but CASCADE DELETE is not configured"
                    )

                return False

    async def cleanup_test_data(self, project_id: int, entity_id: int):
        """Clean up any remaining test data."""
        async with self.session_maker() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))

            try:
                # Delete entity first (in case cascade didn't work)
                await session.execute(
                    text("DELETE FROM entity WHERE id = :entity_id"), {"entity_id": entity_id}
                )

                # Delete project
                await session.execute(
                    text("DELETE FROM project WHERE id = :project_id"), {"project_id": project_id}
                )

                await session.commit()
                print("🧹 Cleaned up any remaining test data")

            except Exception as e:
                print(f"⚠️  Error during cleanup: {e}")
                await session.rollback()

    async def restore_backup(self):
        """Restore database from backup."""
        if self.backup_path.exists():
            print("🔄 Restoring database from backup...")
            import shutil

            shutil.copy2(self.backup_path, self.db_path)
            print("✅ Database restored from backup")

            # Remove backup file
            self.backup_path.unlink()
            print("🗑️  Backup file removed")
        else:
            print("⚠️  No backup file found to restore")

    async def run_test(self) -> bool:
        """Run the complete cascade delete test."""
        print("🧪 Production Database CASCADE DELETE Test")
        print("=" * 50)

        try:
            await self.setup()

            # Check if foreign keys are enabled
            fk_enabled = await self.check_foreign_keys_enabled()
            print(f"🔐 Foreign keys enabled: {fk_enabled}")

            if not fk_enabled:
                print(
                    "❌ Foreign keys are not enabled - this test requires foreign key enforcement"
                )
                return False

            # Check current schema
            has_cascade = await self.check_schema()

            # Create test data
            project_id, entity_id = await self.create_test_data()

            # Verify test data exists
            if not await self.verify_test_data_exists(project_id, entity_id):
                return False

            # Test cascade delete
            cascade_works = await self.test_cascade_delete(project_id, entity_id)

            # Clean up any remaining test data
            await self.cleanup_test_data(project_id, entity_id)

            print("\n" + "=" * 50)
            print("🧪 TEST RESULTS:")
            print(f"  Schema has CASCADE DELETE: {has_cascade}")
            print(f"  CASCADE DELETE works: {cascade_works}")

            if has_cascade and cascade_works:
                print(
                    "✅ PASS: Foreign key constraints are properly configured with CASCADE DELETE"
                )
            elif not has_cascade and not cascade_works:
                print("❌ FAIL: Foreign key constraints are missing CASCADE DELETE configuration")
                print("💡 This confirms issue #254 - migration a1b2c3d4e5f6 is needed")
            else:
                print("⚠️  MIXED: Unexpected result combination")

            return cascade_works

        except Exception as e:
            print(f"💥 Test failed with error: {e}")
            return False
        finally:
            await self.cleanup()
            # Always restore backup to avoid leaving test data
            await self.restore_backup()


async def main():
    """Main test function."""
    import argparse

    parser = argparse.ArgumentParser(description="Test cascade delete on production database")
    parser.add_argument(
        "--db-path", type=Path, help="Path to database file (default: ~/.basic-memory/memory.db)"
    )
    parser.add_argument("--no-backup", action="store_true", help="Skip creating backup (dangerous)")

    args = parser.parse_args()

    if args.no_backup:
        print("⚠️  WARNING: Running without backup!")
        response = input("Are you sure? Type 'yes' to continue: ")
        if response.lower() != "yes":
            print("❌ Aborted")
            return

    test = ProductionCascadeTest(args.db_path)
    success = await test.run_test()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
