"""Exact-text page resolution must not silently cite a different revision."""

import hashlib

import pytest
from pydantic import ValidationError

from basic_memory.schemas.document_page_map import DocumentPageMapV1, DocumentPageRangeV1


def test_unicode_spans_resolve_pages_and_reject_drift() -> None:
    body = "é🙂\nsecond"
    page_map = DocumentPageMapV1(
        body_checksum="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
        body_length=len(body),
        pages=(
            DocumentPageRangeV1(page=1, start=0, end=3),
            DocumentPageRangeV1(page=2, start=3, end=len(body)),
        ),
    )
    assert page_map.resolve_span(body, start=0, end=2) == (1,)
    assert page_map.resolve_span(body, start=3, end=len(body)) == (2,)
    assert page_map.resolve_span(body, start=1, end=4) == (1, 2)
    with pytest.raises(ValueError, match="does not match"):
        page_map.resolve_span(body.replace("second", "edited"), start=0, end=1)
    for start, end in [(-1, 1), (0, 0), (0, len(body) + 1)]:
        with pytest.raises(ValueError, match="nonempty"):
            page_map.resolve_span(body, start=start, end=end)


@pytest.mark.parametrize(
    "pages",
    [
        [{"page": 1, "start": 1, "end": 3}],
        [{"page": 2, "start": 0, "end": 3}],
        [{"page": 1, "start": 0, "end": 2}],
        [{"page": 1, "start": 2, "end": 1}],
    ],
)
def test_invalid_page_partitions_are_rejected(pages: list[dict[str, int]]) -> None:
    with pytest.raises(ValidationError):
        DocumentPageMapV1.model_validate(
            {"body_checksum": "sha256:" + "a" * 64, "body_length": 3, "pages": pages}
        )
