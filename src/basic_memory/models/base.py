"""Base model class for SQLAlchemy models."""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all models"""

    if TYPE_CHECKING:
        id: int
