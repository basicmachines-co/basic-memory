"""Whether this host can run vector search.

Semantic search is always on, but SQLite needs the sqlite-vec extension, and some
Python builds cannot load SQLite extensions at all (python.org Python on macOS,
#711). Those hosts run keyword-only. Postgres keeps vectors in pgvector and has no
such fallback.
"""

import sqlite3
from functools import cache

from basic_memory.config import BasicMemoryConfig, DatabaseBackend


@cache
def sqlite_vector_runtime_available() -> bool:
    """Whether this Python can load sqlite-vec. The answer cannot change in-process."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    connection = sqlite3.connect(":memory:")
    try:
        if not hasattr(connection, "enable_load_extension"):
            return False
        try:
            connection.enable_load_extension(True)
            connection.load_extension(sqlite_vec.loadable_path())
        except (AttributeError, sqlite3.OperationalError):
            return False
        return True
    finally:
        connection.close()


def semantic_runtime_available(app_config: BasicMemoryConfig) -> bool:
    """Whether vector and hybrid search can run for this configuration's backend."""
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        return True
    return sqlite_vector_runtime_available()
