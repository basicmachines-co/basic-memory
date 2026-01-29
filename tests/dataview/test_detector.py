"""Tests for Dataview Detector."""

import pytest

from basic_memory.dataview.detector import DataviewDetector, DataviewBlock


class TestDetectorCodeblocks:
    """Test detection of codeblock queries."""

    def test_detect_single_codeblock(self):
        """Test detecting single codeblock."""
        content = """# Note

```dataview
LIST FROM "1. projects"
```
"""
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].block_type == "codeblock"
        assert blocks[0].query == 'LIST FROM "1. projects"'

    def test_detect_multiple_codeblocks(self):
        """Test detecting multiple codeblocks."""
        content = """# Note

```dataview
LIST FROM "1. projects"
```

Some text.

```dataview
TABLE title, status
```
"""
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 2
        assert blocks[0].query == 'LIST FROM "1. projects"'
        assert blocks[1].query == "TABLE title, status"

    def test_detect_multiline_codeblock(self):
        """Test detecting multiline codeblock."""
        content = """```dataview
TABLE title, status
FROM "1. projects"
WHERE status = "active"
SORT title ASC
```"""
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert "TABLE title, status" in blocks[0].query
        assert "WHERE status" in blocks[0].query

    def test_ignore_non_dataview_codeblocks(self):
        """Test that non-dataview codeblocks are ignored."""
        content = """```python
print("hello")
```

```dataview
LIST
```
"""
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].query == "LIST"

    def test_handle_empty_codeblock(self):
        """Test handling empty dataview codeblock."""
        content = """```dataview
```"""
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].query == ""


class TestDetectorInlineQueries:
    """Test detection of inline queries."""

    def test_detect_single_inline(self):
        """Test detecting single inline query."""
        content = "Status: `= this.status`"
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].block_type == "inline"
        assert blocks[0].query == "this.status"

    def test_detect_multiple_inline(self):
        """Test detecting multiple inline queries."""
        content = "Status: `= this.status` Priority: `= this.priority`"
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 2
        assert blocks[0].query == "this.status"
        assert blocks[1].query == "this.priority"

    def test_detect_inline_with_function(self):
        """Test detecting inline query with function."""
        content = "Count: `= length(this.tags)`"
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].query == "length(this.tags)"

    def test_detect_inline_with_whitespace(self):
        """Test detecting inline query with whitespace."""
        content = "Value: `=   this.value   `"
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].query == "this.value"


class TestDetectorLineTracking:
    """Test line number tracking."""

    def test_track_codeblock_lines(self):
        """Test tracking line numbers for codeblocks."""
        content = """Line 1
Line 2
```dataview
LIST
```
Line 6"""
        blocks = DataviewDetector.detect_queries(content)
        assert blocks[0].start_line == 2  # 0-indexed
        assert blocks[0].end_line == 4

    def test_track_inline_lines(self):
        """Test tracking line numbers for inline queries."""
        content = """Line 1
Line 2 with `= this.value`
Line 3"""
        blocks = DataviewDetector.detect_queries(content)
        assert blocks[0].start_line == 1  # 0-indexed
        assert blocks[0].end_line == 1


class TestDetectorMixed:
    """Test detection of mixed query types."""

    def test_detect_mixed_queries(self, markdown_with_dataview):
        """Test detecting both codeblock and inline queries."""
        blocks = DataviewDetector.detect_queries(markdown_with_dataview)
        codeblocks = [b for b in blocks if b.block_type == "codeblock"]
        inline = [b for b in blocks if b.block_type == "inline"]
        assert len(codeblocks) == 2
        assert len(inline) == 2


class TestDetectorHelpers:
    """Test helper methods."""

    def test_has_dataview_queries_true(self):
        """Test has_dataview_queries returns True."""
        content = "```dataview\nLIST\n```"
        assert DataviewDetector.has_dataview_queries(content) is True

    def test_has_dataview_queries_false(self):
        """Test has_dataview_queries returns False."""
        content = "# Just a note\n\nNo queries here."
        assert DataviewDetector.has_dataview_queries(content) is False

    def test_extract_query_text(self):
        """Test extracting just query text."""
        content = """```dataview
LIST
```

`= this.value`"""
        queries = DataviewDetector.extract_query_text(content)
        assert len(queries) == 2
        assert queries[0] == "LIST"
        assert queries[1] == "this.value"


class TestDetectorEdgeCases:
    """Test edge cases."""

    def test_handle_no_queries(self):
        """Test handling content with no queries."""
        content = "# Just a note\n\nNo queries here."
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 0

    def test_handle_empty_content(self):
        """Test handling empty content."""
        blocks = DataviewDetector.detect_queries("")
        assert len(blocks) == 0

    def test_handle_unclosed_codeblock(self):
        """Test handling unclosed codeblock."""
        content = """```dataview
LIST FROM "1. projects"
"""
        blocks = DataviewDetector.detect_queries(content)
        # Should not detect unclosed codeblock
        assert len(blocks) == 0

    def test_handle_nested_backticks(self):
        """Test handling nested backticks."""
        content = "Text with `code` and `= this.value` inline."
        blocks = DataviewDetector.detect_queries(content)
        assert len(blocks) == 1
        assert blocks[0].query == "this.value"
