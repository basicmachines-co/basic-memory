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
