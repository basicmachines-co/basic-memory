"""API-only database search contract; effective authorization scope is required."""

from typing import Annotated

from pydantic import BaseModel, Field

from basic_memory.schemas.search import SearchQuery, SearchResult


class MultiProjectSearchQuery(SearchQuery):
    """Cloud resolves authorization before passing internal database project IDs."""

    project_ids: list[Annotated[int, Field(strict=True, gt=0)]]


class MultiProjectSearchResult(SearchResult):
    project_id: int
    project_external_id: str


class MultiProjectSearchResponse(BaseModel):
    results: list[MultiProjectSearchResult]
    current_page: int
    page_size: int
    total: int = 0
    total_is_exact: bool = True
    has_more: bool = False
    temporal_applied: bool | None = None
