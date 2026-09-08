"""Page provenance for exact raw extraction text, independent of PDF parsers."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentPageRangeV1(BaseModel):
    """Half-open Unicode code-point offsets in the raw extraction body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def require_ordered_offsets(self) -> "DocumentPageRangeV1":
        if self.end < self.start:
            raise ValueError("page range end cannot precede start")
        return self


class DocumentPageMapV1(BaseModel):
    """Ordered page ranges bound to one exact raw Markdown body checksum.

    This remains extraction provenance after enrichment; offsets never address
    the rewritten agent body or the frontmatter of a serialized note.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["unicode_code_point"] = "unicode_code_point"
    body_checksum: str = Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")
    body_length: int = Field(ge=0, strict=True)
    pages: tuple[DocumentPageRangeV1, ...]

    @model_validator(mode="after")
    def require_complete_partition(self) -> "DocumentPageMapV1":
        end = 0
        for number, page in enumerate(self.pages, start=1):
            if page.page != number or page.start != end:
                raise ValueError("page map must cover ordered consecutive pages without gaps")
            end = page.end
        if end != self.body_length:
            raise ValueError("page map must cover the entire raw body")
        return self

    def resolve_span(self, body: str, *, start: int, end: int) -> tuple[int, ...]:
        """Resolve a nonempty raw-body span, rejecting stale or rewritten text."""
        checksum = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        if len(body) != self.body_length or checksum != self.body_checksum:
            raise ValueError("body does not match extraction page map")
        if not 0 <= start < end <= self.body_length:
            raise ValueError("span must be nonempty and within the raw body")
        return tuple(page.page for page in self.pages if page.start < end and start < page.end)
