"""Composition coverage for external vector cleanup in full project indexing."""

from unittest.mock import Mock

from basic_memory.index.local_dependencies import LocalIndexProjectDependencies
from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
from basic_memory.indexing.project_index_maintenance import (
    RepositoryProjectIndexMaintenanceStore,
    StoreProjectIndexMaintenanceRunner,
)


class RuntimeFactoryEntityService:
    app_config = None

    async def resolve_permalink(self, *args: object, **kwargs: object) -> str:
        return "local"


def test_full_project_runtime_forwards_external_vector_cleaner() -> None:
    cleaner = Mock()
    dependencies = LocalIndexProjectDependencies(
        file_service=Mock(),
        file_indexer=Mock(),
        file_batch_indexer=Mock(),
        session_maker=Mock(),
        project_id=42,
        entity_repository=Mock(),
        relation_repository=Mock(),
        link_resolver=Mock(),
        search_service=Mock(),
        entity_service=RuntimeFactoryEntityService(),
        external_vector_cleaner=cleaner,
    )

    runtime = LocalProjectIndexRuntimeFactory().runtime_from_dependencies(dependencies)

    assert isinstance(runtime.maintenance_runner, StoreProjectIndexMaintenanceRunner)
    assert isinstance(
        runtime.maintenance_runner.delete_store,
        RepositoryProjectIndexMaintenanceStore,
    )
    assert runtime.maintenance_runner.delete_store.external_vector_cleaner is cleaner
