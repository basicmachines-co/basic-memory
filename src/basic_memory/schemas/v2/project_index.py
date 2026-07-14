"""V2 project-index route response schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from basic_memory.schemas.project_index import ProjectIndexRunResponse


class ProjectIndexStartedResponse(BaseModel):
    """Acknowledgement that a project-index run was scheduled in the background."""

    status: Literal["index_started"] = "index_started"
    message: str = Field(description="Human-readable scheduling confirmation")


# One project-index route, two outcomes: run_in_background schedules a run and
# acknowledges it; a foreground request runs the coordinator inline and reports
# its counts.
type ProjectIndexResponse = ProjectIndexRunResponse | ProjectIndexStartedResponse
