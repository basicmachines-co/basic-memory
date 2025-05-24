"""Request and response schemas for prompt-related operations."""

from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field

from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import EntitySummary, ObservationSummary, RelationSummary


class PromptContextItem(BaseModel):
    """Container for primary and related results to render in a prompt."""

    primary_results: List[EntitySummary]
    related_results: List[EntitySummary | ObservationSummary | RelationSummary]


class ContinueConversationRequest(BaseModel):
    """Request for generating a continue conversation prompt.

    Used to provide context for continuing a conversation on a specific topic
    or with recent activity from a given timeframe.
    """

    topic: Optional[str] = Field(None, description="Topic or keyword to search for")
    timeframe: Optional[TimeFrame] = Field(
        None, description="How far back to look for activity (e.g. '1d', '1 week')"
    )
    # Limit depth to max 2 for performance reasons - higher values cause significant slowdown
    search_items_limit: int = Field(
        5,
        description="Maximum number of search results to include in context (max 10)",
        ge=1,
        le=10,
    )
    depth: int = Field(
        1,
        description="How many relationship 'hops' to follow when building context (max 5)",
        ge=1,
        le=5,
    )
    # Limit related items to prevent overloading the context
    related_items_limit: int = Field(
        5, description="Maximum number of related items to include in context (max 10)", ge=1, le=10
    )


class SearchPromptRequest(BaseModel):
    """Request for generating a search results prompt.

    Used to format search results into a prompt with context and suggestions.
    """

    query: str = Field(..., description="The search query text")
    timeframe: Optional[TimeFrame] = Field(
        None, description="Optional timeframe to limit results (e.g. '1d', '1 week')"
    )


class PromptMetadata(BaseModel):
    """Metadata about a prompt response.

    Contains statistical information about the prompt generation process
    and results, useful for debugging and UI display.
    """

    query: Optional[str] = Field(None, description="The original query or topic")
    timeframe: Optional[str] = Field(None, description="The timeframe used for filtering")
    search_count: int = Field(0, description="Number of search results found")
    context_count: int = Field(0, description="Number of context items retrieved")
    observation_count: int = Field(0, description="Total number of observations included")
    relation_count: int = Field(0, description="Total number of relations included")
    total_items: int = Field(0, description="Total number of all items included in the prompt")
    search_limit: int = Field(0, description="Maximum search results requested")
    context_depth: int = Field(0, description="Context depth used")
    related_limit: int = Field(0, description="Maximum related items requested")
    generated_at: str = Field(..., description="ISO timestamp when this prompt was generated")


class PromptResponse(BaseModel):
    """Response containing the rendered prompt.

    Includes both the rendered prompt text and the context that was used
    to render it, for potential client-side use.
    """

    prompt: str = Field(..., description="The rendered prompt text")
    context: Dict[str, Any] = Field(..., description="The context used to render the prompt")
    metadata: PromptMetadata = Field(
        ..., description="Metadata about the prompt generation process"
    )
