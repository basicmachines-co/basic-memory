class FileOperationError(Exception):
    """Raised when file operations fail"""

    pass


class EntityNotFoundError(Exception):
    """Raised when an entity cannot be found"""

    pass


class EntityCreationError(Exception):
    """Raised when an entity cannot be created"""

    pass


class DirectoryOperationError(Exception):
    """Raised when directory operations fail"""

    pass


class SyncFatalError(Exception):
    """Raised when sync encounters a fatal error that prevents continuation.

    Fatal errors include:
    - Project deleted during sync (FOREIGN KEY constraint)
    - Database corruption
    - Critical system failures

    When this exception is raised, the entire sync operation should be terminated
    immediately rather than attempting to continue with remaining files.
    """

    pass
