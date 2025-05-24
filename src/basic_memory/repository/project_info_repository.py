from basic_memory.repository.repository import Repository
from basic_memory.models.project import Project


class ProjectInfoRepository(Repository):
    """Repository for statistics queries."""

    def __init__(self, session_maker):
        # Initialize with Project model as a reference
        super().__init__(session_maker, Project)
