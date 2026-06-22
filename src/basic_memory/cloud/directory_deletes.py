"""Shared directory-delete service facade.

Runtime-specific callers provide the tenant/session boundary and the file
cleanup enqueuer. The core service owns request acceptance and response shaping.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing import (
    DirectoryDeleteAcceptanceRequest,
    DirectoryDeleteRejected,
    DirectoryDeleteRejection,
    DirectoryDeleteRuntime,
    accept_directory_delete,
    finish_directory_delete_acceptance,
    normalize_directory_delete_path,
)


class DirectoryDeleteSessionMaker(Protocol):
    """Session factory capability needed by directory-delete acceptance."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


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
        session_maker: DirectoryDeleteSessionMaker,
        runtime: DirectoryDeleteRuntime,
    ) -> None:
        self.session_maker = session_maker
        self.runtime = runtime

    async def delete_directory(
        self,
        *,
        tenant_id: UUID,
        project_external_id: str,
        directory: str,
    ) -> tuple[int, dict[str, object]]:
        """Delete directory entities immediately and queue file cleanup in the background."""
        request = DirectoryDeleteAcceptanceRequest(
            tenant_id=tenant_id,
            project_external_id=project_external_id,
            directory=directory,
        )
        try:
            async with self.session_maker() as session:
                async with session.begin():
                    accepted = await accept_directory_delete(
                        session,
                        request=request,
                        store=self.runtime.store,
                    )
        except DirectoryDeleteRejected as error:
            raise directory_delete_service_error_from_rejection(error.rejection) from error

        result = await finish_directory_delete_acceptance(
            request=request,
            accepted=accepted,
            enqueuer=self.runtime.file_delete_enqueuer,
        )
        status_code = 500 if result.file_delete_status == "failed" else 200
        return status_code, result.to_response_payload()

    @staticmethod
    def normalize_directory_path(directory: str) -> str:
        """Normalize a project-relative directory path or reject traversal."""
        try:
            return normalize_directory_delete_path(directory)
        except ValueError as exc:
            raise DirectoryDeleteServiceError(400, "Invalid directory path") from exc
