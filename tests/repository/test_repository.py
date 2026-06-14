"""Test repository implementation."""

from datetime import datetime, UTC
import pytest
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from basic_memory import db
from basic_memory.models import Base
from basic_memory.repository.repository import Repository


class ModelTest(Base):
    """Test model for repository tests."""

    __tablename__ = "test_model"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.fixture
def repository():
    """Create a test repository."""
    return Repository(ModelTest)


@pytest.mark.asyncio
async def test_add(repository, session_maker):
    """Test bulk creation of entities."""
    # Create test instances
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        # Verify we can find in db
        found = await repository.find_by_id(session, "test_add")
    assert found is not None
    assert found.name == "Test Add"


@pytest.mark.asyncio
async def test_add_all(repository, session_maker):
    """Test bulk creation of entities."""
    # Create test instances
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(3)]
    async with db.scoped_session(session_maker) as session:
        await repository.add_all(session, instances)

        # Verify we can find them in db
        found = await repository.find_by_id(session, "test_0")
    assert found is not None
    assert found.name == "Test 0"


@pytest.mark.asyncio
async def test_add_all_no_return(repository, session_maker):
    """Bulk inserts can skip the follow-up reload when callers do not need rows back."""
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(3)]

    async with db.scoped_session(session_maker) as session:
        inserted = await repository.add_all_no_return(session, instances)

        assert inserted == 3
        found = await repository.find_by_id(session, "test_0")
    assert found is not None
    assert found.name == "Test 0"


@pytest.mark.asyncio
async def test_bulk_create(repository, session_maker):
    """Test bulk creation of entities."""
    # Create test instances
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(3)]

    # Bulk create
    async with db.scoped_session(session_maker) as session:
        await repository.create_all(session, [instance.__dict__ for instance in instances])

        # Verify we can find them in db
        found = await repository.find_by_id(session, "test_0")
    assert found is not None
    assert found.name == "Test 0"


@pytest.mark.asyncio
async def test_find_all(repository, session_maker):
    """Test finding multiple entities by IDs."""
    # Create test data
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(5)]
    async with db.scoped_session(session_maker) as session:
        await repository.create_all(session, [instance.__dict__ for instance in instances])

        found = await repository.find_all(session, limit=3)
    assert len(found) == 3


@pytest.mark.asyncio
async def test_find_by_ids(repository, session_maker):
    """Test finding multiple entities by IDs."""
    # Create test data
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(5)]
    async with db.scoped_session(session_maker) as session:
        await repository.create_all(session, [instance.__dict__ for instance in instances])

        # Test finding subset of entities
        ids_to_find = ["test_0", "test_2", "test_4"]
        found = await repository.find_by_ids(session, ids_to_find)
        assert len(found) == 3
        assert sorted([e.id for e in found]) == sorted(ids_to_find)

        # Test finding with some non-existent IDs
        mixed_ids = ["test_0", "nonexistent", "test_4"]
        partial_found = await repository.find_by_ids(session, mixed_ids)
        assert len(partial_found) == 2
        assert sorted([e.id for e in partial_found]) == ["test_0", "test_4"]

        # Test with empty list
        empty_found = await repository.find_by_ids(session, [])
        assert len(empty_found) == 0

        # Test with all non-existent IDs
        not_found = await repository.find_by_ids(session, ["fake1", "fake2"])
        assert len(not_found) == 0


@pytest.mark.asyncio
async def test_delete_by_ids(repository, session_maker):
    """Test finding multiple entities by IDs."""
    # Create test data
    instances = [ModelTest(id=f"test_{i}", name=f"Test {i}") for i in range(5)]
    async with db.scoped_session(session_maker) as session:
        await repository.create_all(session, [instance.__dict__ for instance in instances])

        # Test delete subset of entities
        ids_to_delete = ["test_0", "test_2", "test_4"]
        deleted_count = await repository.delete_by_ids(session, ids_to_delete)
        assert deleted_count == 3

        # Test finding subset of entities
        ids_to_find = ["test_1", "test_3"]
        found = await repository.find_by_ids(session, ids_to_find)
        assert len(found) == 2
        assert sorted([e.id for e in found]) == sorted(ids_to_find)

        assert await repository.find_by_id(session, ids_to_delete[0]) is None
        assert await repository.find_by_id(session, ids_to_delete[1]) is None
        assert await repository.find_by_id(session, ids_to_delete[2]) is None


@pytest.mark.asyncio
async def test_update(repository, session_maker):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        instance = ModelTest(id="test_add", name="Updated")

        # Find recently modified
        modified = await repository.update(session, instance.id, {"name": "Updated"})
    assert modified is not None
    assert modified.name == "Updated"


@pytest.mark.asyncio
async def test_update_fields(repository, session_maker):
    """Column-only updates can skip eager reloads on write-heavy paths."""
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        updated = await repository.update_fields(session, instance.id, {"name": "Updated"})

        assert updated is True
        found = await repository.find_by_id(session, instance.id)
    assert found is not None
    assert found.name == "Updated"


@pytest.mark.asyncio
async def test_update_fields_not_found(repository, session_maker):
    """Column-only updates still report when no row matched."""
    async with db.scoped_session(session_maker) as session:
        updated = await repository.update_fields(session, "missing", {"name": "Updated"})

    assert updated is False


@pytest.mark.asyncio
async def test_update_model(repository, session_maker):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        instance.name = "Updated"

        # Find recently modified
        modified = await repository.update(session, instance.id, instance)
    assert modified is not None
    assert modified.name == "Updated"


@pytest.mark.asyncio
async def test_update_model_not_found(repository, session_maker):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        modified = await repository.update(
            session, "0", {}
        )  # Use string ID for Postgres compatibility
    assert modified is None


@pytest.mark.asyncio
async def test_count(repository, session_maker):
    """Test bulk creation of entities."""
    # Create test instances
    instance = ModelTest(id="test_add", name="Test Add")
    async with db.scoped_session(session_maker) as session:
        await repository.add(session, instance)

        # Verify we can count in db
        count = await repository.count(session)
    assert count == 1
