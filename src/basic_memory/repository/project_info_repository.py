from basic_memory.repository.repository import Repository


class ProjectInfoRepository(Repository):
    """Repository for statistics queries."""

    def __init__(self, session_maker):
        # Initialize with a dummy model since we're just using the execute_query method
        super().__init__(session_maker, None)  # type: ignore
