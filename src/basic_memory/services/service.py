"""Base service class."""

from typing import TypeVar, Generic, List, Sequence

from basic_memory.models import Base

T = TypeVar("T", bound=Base)


class BaseService(Generic[T]):
    """Base service that takes a repository."""

    def __init__(self, repository):
        """Initialize service with repository."""
        self.repository = repository
