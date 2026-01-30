"""Unit test for Dataview detector."""

from basic_memory.dataview.detector import DataviewDetector


def test_detector_finds_codeblock():
    """Test that detector finds codeblock queries."""
    content = """---
title: Test Note
---
# Test Note

```dataview
LIST
FROM ""
```
"""
    
    detector = DataviewDetector()
    blocks = detector.detect_queries(content)
    
    print(f"\nContent:\n{content}")
    print(f"\nBlocks found: {len(blocks)}")
    for block in blocks:
        print(f"  - {block}")
        print(f"    Query: {repr(block.query)}")
    
    assert len(blocks) == 1, f"Expected 1 block, found {len(blocks)}"
    assert blocks[0].block_type == "codeblock"
    assert "LIST" in blocks[0].query
