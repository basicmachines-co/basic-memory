"""Runtime-neutral directory-delete service facade.

Runtime-specific callers provide the session boundary and the file cleanup
enqueuer. The core service owns request acceptance and response shaping.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteAcceptanceRequest,
    DirectoryDeleteAcceptedResult,
    DirectoryDeleteRejected,
    DirectoryDeleteRejection,
    DirectoryDeleteRuntime,
    accept_directory_delete,
    finish_directory_delete_acceptance,
    normalize_directory_delete_path,
)
from basic_memory.read_cache import (
    ReadCacheInvalidator,
    finish_project_read_cache_invalidation,
)


class DirectoryDeleteServiceError(Exception):
    """Structured directory-delete service error for route adapters."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def directory_delete_service_error_from_rejection(
    rejection: DirectoryDeleteRejection,
) -> DirectoryDeleteServiceError:
    """Map core directory-delete rejections into route-facing errors."""
    return DirectoryDeleteServiceError(
        rejection.kind.http_status_code,
        rejection.detail,
    )


class DirectoryDeleteService:
    """Accept directory deletes into project DB state before storage cleanup begins."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        runtime: DirectoryDeleteRuntime,
    ) -> None:
        self.session_maker = session_maker
        self.runtime = runtime

    async def delete_directory(
        self,
        *,
        project_external_id: str,
        directory: str,
        read_cache: ReadCacheInvalidator | None = None,
    ) -> DirectoryDeleteAcceptedResult:
        """Delete directory entities immediately and queue file cleanup in the background.

        The typed result carries the route status (``http_status_code``) and the
        existing response contract (``to_response_payload``).
        """
        request = DirectoryDeleteAcceptanceRequest(
            project_external_id=project_external_id,
            directory=directory,
        )
        delete_may_have_committed = False
        try:
            # scoped_session enables `PRAGMA foreign_keys=ON` for SQLite; this bulk
            # delete issues a Core DELETE on entity and relies on ON DELETE CASCADE
            # for note_content/observations/relations, which a raw session_maker()
            # connection (foreign_keys OFF by default) would leave orphaned.
            async with db.scoped_session(self.session_maker) as session:
                accepted = await accept_directory_delete(
                    session,
                    request=request,
                    store=self.runtime.store,
                )
                delete_may_have_committed = bool(accepted.files)
        except DirectoryDeleteRejected as error:
            raise directory_delete_service_error_from_rejection(error.rejection) from error
        finally:
            if delete_may_have_committed and read_cache is not None:
                # Acceptance commits entity and search deletion before storage cleanup.
                # Finish the generation bump even when cancellation interrupts the
                # transaction exit, then preserve that cancellation.
                await finish_project_read_cache_invalidation(
                    read_cache,
                    project_external_id,
                )

        try:
            result = await finish_directory_delete_acceptance(
                request=request,
                accepted=accepted,
                enqueuer=self.runtime.file_delete_enqueuer,
            )

            # Trigger: notes outside the deleted directory linked into it.
            # Why: the delete cascaded their relation rows away, but those sources own
            #   matching search_index relation rows that now dangle; without a reindex
            #   they linger until an unrelated rebuild.
            # Outcome: reindex each surviving source inline when the runtime provides a
            #   refresher (local); queued runtimes consume the ids from the result.
            if accepted.relation_cleanup_entity_ids and self.runtime.relation_cleanup_refresher:
                await self.runtime.relation_cleanup_refresher.refresh_relation_sources(
                    sorted(accepted.relation_cleanup_entity_ids)
                )

            return result
        finally:
            if accepted.files and read_cache is not None:
                # Cleanup and relation refresh can publish additional state or fail
                # after partial progress. Close the fill window in either case.
                await finish_project_read_cache_invalidation(
                    read_cache,
                    project_external_id,
                )

    @staticmethod
    def normalize_directory_path(directory: str) -> str:
        """Normalize a project-relative directory path or reject traversal."""
        try:
            return normalize_directory_delete_path(directory)
        except ValueError as exc:
            raise DirectoryDeleteServiceError(400, "Invalid directory path") from exc
