class FileOperationError(Exception):
    """Raised when file operations fail"""

    pass


class EntityNotFoundError(Exception):
    """Raised when an entity cannot be found"""

    pass


class AmbiguousIdentifierError(Exception):
    """Raised when a non-exact identifier matches multiple entities under strict resolution.

    Strict resolution backs destructive operations (edit, move). It must never silently pick
    one of several same-title notes (e.g. an original plus a ``-1`` duplicate), so the caller is
    told to disambiguate with an exact permalink or external_id. Non-strict resolution (wiki
    links, reads) keeps its shortest-path preference and does not raise. See issue #1148.
    """

    def __init__(self, identifier: str, candidates: list[tuple[str | None, str]]) -> None:
        self.identifier = identifier
        self.candidates = candidates
        listing = "; ".join(
            f"{file_path} (permalink: {permalink})" if permalink else f"{file_path} (no permalink)"
            for permalink, file_path in candidates
        )
        super().__init__(
            f"Ambiguous identifier '{identifier}' matches {len(candidates)} notes: {listing}. "
            "Pass an exact permalink or external_id to disambiguate."
        )


class EntityCreationError(Exception):
    """Raised when an entity cannot be created"""

    pass


class EntityAlreadyExistsError(EntityCreationError):
    """Raised when an entity file already exists"""

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
