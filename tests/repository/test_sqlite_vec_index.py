"""sqlite-vec adapter behavior that does not need a database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError as SAOperationalError

from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import VectorIndexScope
from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex


@pytest.mark.asyncio
async def test_missing_extension_support_is_a_dependency_error() -> None:
    """A Python whose sqlite3 cannot load extensions gets the keyword-only fallback error.

    aiosqlite exposes ``enable_load_extension`` even when the wrapped connection does
    not, so the attribute probe passes and only the call reveals it (#711). The adapter
    must raise the same typed error the repository does, or a scoped vector search on
    such a host answers 500 instead of an actionable 400.
    """
    index = SQLiteVecIndex(
        MagicMock(),
        VectorIndexScope(namespace="test", embedding_identity="test", dimensions=4),
    )
    driver = SimpleNamespace(
        enable_load_extension=AsyncMock(side_effect=AttributeError("enable_load_extension"))
    )
    connection = AsyncMock()
    connection.get_raw_connection = AsyncMock(
        return_value=SimpleNamespace(driver_connection=driver)
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=SAOperationalError("SELECT vec_version()", {}, Exception("no such function"))
    )
    session.connection = AsyncMock(return_value=connection)

    with pytest.raises(SemanticDependenciesMissingError, match="extension loading"):
        await index._ensure_loaded(session)
