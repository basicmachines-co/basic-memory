"""Test repository implementation."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from basic_memory.models import Base
from basic_memory.repository.repository import Repository


class TestModel(Base):
    """Test model for repository tests."""

    __tablename__ = "test_model"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


@pytest.fixture
def repository(session_maker):
    """Create a test repository."""
    return Repository(session_maker, TestModel)


@pytest.mark.asyncio
async def test_add(repository):
    """Test bulk creation of entities."""
    # Create test instances
    instance = TestModel(id="test_add", name="Test Add")
    await repository.add(instance)

    # Verify we can find in db
    found = await repository.find_by_id("test_add")
    assert found is not None
    assert found.name == "Test Add"


@pytest.mark.asyncio
async def test_add_all(repository):
    """Test bulk creation of entities."""
    # Create test instances
    instances = [TestModel(id=f"test_{i}", name=f"Test {i}") for i in range(3)]
    await repository.add_all(instances)

    # Verify we can find them in db
    found = await repository.find_by_id("test_0")
    assert found is not None
    assert found.name == "Test 0"


@pytest.mark.asyncio
async def test_bulk_create(repository):
    """Test bulk creation of entities."""
    # Create test instances
    instances = [TestModel(id=f"test_{i}", name=f"Test {i}") for i in range(3)]

    # Bulk create
    await repository.create_all([instance.__dict__ for instance in instances])

    # Verify we can find them in db
    found = await repository.find_by_id("test_0")
    assert found is not None
    assert found.name == "Test 0"


@pytest.mark.asyncio
async def test_find_by_ids(repository):
    """Test finding multiple entities by IDs."""
    # Create test data
    instances = [TestModel(id=f"test_{i}", name=f"Test {i}") for i in range(5)]
    await repository.create_all([instance.__dict__ for instance in instances])

    # Test finding subset of entities
    ids_to_find = ["test_0", "test_2", "test_4"]
    found = await repository.find_by_ids(ids_to_find)
    assert len(found) == 3
    assert sorted([e.id for e in found]) == sorted(ids_to_find)

    # Test finding with some non-existent IDs
    mixed_ids = ["test_0", "nonexistent", "test_4"]
    partial_found = await repository.find_by_ids(mixed_ids)
    assert len(partial_found) == 2
    assert sorted([e.id for e in partial_found]) == ["test_0", "test_4"]

    # Test with empty list
    empty_found = await repository.find_by_ids([])
    assert len(empty_found) == 0

    # Test with all non-existent IDs
    not_found = await repository.find_by_ids(["fake1", "fake2"])
    assert len(not_found) == 0


@pytest.mark.asyncio
async def test_delete_by_ids(repository):
    """Test finding multiple entities by IDs."""
    # Create test data
    instances = [TestModel(id=f"test_{i}", name=f"Test {i}") for i in range(5)]
    await repository.create_all([instance.__dict__ for instance in instances])

    # Test delete subset of entities
    ids_to_delete = ["test_0", "test_2", "test_4"]
    deleted_count = await repository.delete_by_ids(ids_to_delete)
    assert deleted_count == 3

    # Test finding subset of entities
    ids_to_find = ["test_1", "test_3"]
    found = await repository.find_by_ids(ids_to_find)
    assert len(found) == 2
    assert sorted([e.id for e in found]) == sorted(ids_to_find)

    assert await repository.find_by_id(ids_to_delete[0]) is None
    assert await repository.find_by_id(ids_to_delete[1]) is None
    assert await repository.find_by_id(ids_to_delete[2]) is None


@pytest.mark.asyncio
async def test_update(repository):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    instance = TestModel(id="test_add", name="Test Add")
    await repository.add(instance)

    instance = TestModel(id="test_add", name="Updated")

    # Find recently modified
    modified = await repository.update(instance.id, {"name": "Updated"})
    assert modified is not None
    assert modified.name == "Updated"


@pytest.mark.asyncio
async def test_update_model(repository):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    instance = TestModel(id="test_add", name="Test Add")
    await repository.add(instance)

    instance.name = "Updated"

    # Find recently modified
    modified = await repository.update(instance.id, instance)
    assert modified is not None
    assert modified.name == "Updated"


@pytest.mark.asyncio
async def test_find_modified_since(repository):
    """Test finding entities modified since a timestamp."""
    # Create initial test data
    now = datetime.now(timezone.utc)
    base_instances = [
        TestModel(
            id=f"test_{i}", 
            name=f"Test {i}",
        ) for i in range(5)
    ]
    await repository.create_all([instance.__dict__ for instance in base_instances])

    # Update some instances to have recent changes
    cutoff_time = now - timedelta(hours=1)
    recent_updates = ["test_1", "test_3"]
    
    for id in recent_updates:
        await repository.update(id, {"name": f"Updated {id}"})

    # Find recently modified
    modified = await repository.find_modified_since(cutoff_time)
    assert len(modified) == 5


@pytest.mark.asyncio
async def test_find_modified_since_invalid_model():
    """Test finding modified entities on model without updated_at."""
    class InvalidModel(Base):
        __tablename__ = "invalid_model"
        id: Mapped[str] = mapped_column(String(255), primary_key=True)
        name: Mapped[str] = mapped_column(String(255))

    repository = Repository(None, InvalidModel)  # type: ignore
    
    with pytest.raises(AttributeError) as exc:
        await repository.find_modified_since(datetime.now(timezone.utc))
    assert "does not have updated_at column" in str(exc.value)