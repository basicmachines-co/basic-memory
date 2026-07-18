"""Runtime-neutral capabilities for scheduling derived index work."""

from typing import Protocol


class EntityVectorSyncScheduler(Protocol):
    def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None: ...


class SearchReindexScheduler(Protocol):
    def schedule_search_reindex(self, *, project_id: int) -> None: ...


class RelationResolutionScheduler(Protocol):
    def schedule_relation_resolution(self, *, project_id: int) -> None: ...


class EntityVectorSyncSearchService(Protocol):
    async def sync_entity_vectors(self, entity_id: int) -> object: ...


class SearchReindexService(Protocol):
    async def reindex_all(self) -> object: ...
