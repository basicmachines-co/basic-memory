"""Transport contract for read-only project integrity diagnostics."""

from pydantic import BaseModel, ConfigDict, computed_field


class IndexIntegrityResponse(BaseModel):
    """Observed anomalies may be transient while indexing is in progress."""

    model_config = ConfigDict(from_attributes=True)

    entities: int
    search_rows: int
    entity_rows: int
    observation_rows: int
    relation_rows: int
    duplicate_keys: int
    duplicate_rows: int
    orphaned_rows: int
    inverted_timestamps: int
    examples: tuple[str, ...]

    @computed_field
    @property
    def has_anomalies(self) -> bool:
        """Whether this observation indicates a damaged search index.

        ``inverted_timestamps`` is deliberately excluded: those columns carry file
        ctime/mtime or authored frontmatter, where updated_at < created_at is a
        valid state rather than corruption. Missing entity rows are included
        because an entity without its search projection is unsearchable; like
        orphaned rows, this can be transient while indexing runs.
        """
        return bool(self.duplicate_keys or self.orphaned_rows or self.entity_rows < self.entities)
