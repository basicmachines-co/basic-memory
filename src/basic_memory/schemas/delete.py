"""Delete operation schemas for the knowledge graph.

This module defines the request schemas for removing entities, relations,
and observations from the knowledge graph. Each operation has specific
implications and safety considerations.

Deletion Hierarchy:
1. Entity deletion removes the entity and all its relations
2. Relation deletion only removes the connection between entities
3. Observation deletion preserves entity and relations

Key Considerations:
- All deletions are permanent
- Entity deletions cascade to relations
- Files are removed along with entities
- Operations are atomic - they fully succeed or fail
"""

from typing import List, Annotated

from annotated_types import MinLen
from pydantic import BaseModel

from basic_memory.schemas.base import Permalink


class DeleteEntitiesRequest(BaseModel):
    """Delete one or more entities from the knowledge graph.

    This operation:
    1. Removes the entity from the database
    2. Deletes all observations attached to the entity
    3. Removes all relations where the entity is source or target
    4. Deletes the corresponding markdown file
    """

    permalinks: Annotated[List[Permalink], MinLen(1)]
