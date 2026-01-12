"""Tests for markdown parser edge cases."""

from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.markdown.entity_parser import EntityParser


@pytest.mark.asyncio
async def test_unicode_content(tmp_path):
    """Test handling of Unicode content including emoji and non-Latin scripts."""
    content = dedent("""
        ---
        type: test
        id: test/unicode
        created: 2024-12-21T14:00:00Z
        modified: 2024-12-21T14:00:00Z
        tags: [unicode, 测试]
        ---
        
        # Unicode Test 🧪
        
        ## Observations
        - [test] Emoji test 👍 #emoji #test (Testing emoji)
        - [中文] Chinese text 测试 #language (Script test)
        - [русский] Russian привет #language (More scripts)
        - [note] Emoji in text 😀 #meta (Category test)
        
        ## Relations
        - tested_by [[测试组件]] (Unicode test)
        - depends_on [[компонент]] (Another test)
        """)

    test_file = tmp_path / "unicode.md"
    test_file.write_text(content, encoding="utf-8")

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(test_file)

    assert "测试" in entity.frontmatter.metadata["tags"]
    assert "chinese" not in entity.frontmatter.metadata["tags"]
    assert "🧪" in entity.content

    # Verify Unicode in observations
    assert any(o.content == "Emoji test 👍 #emoji #test" for o in entity.observations)
    assert any(o.category == "中文" for o in entity.observations)
    assert any(o.category == "русский" for o in entity.observations)

    # Verify Unicode in relations
    assert any(r.target == "测试组件" for r in entity.relations)
    assert any(r.target == "компонент" for r in entity.relations)


@pytest.mark.asyncio
async def test_empty_file(tmp_path):
    """Test handling of empty files."""
    empty_file = tmp_path / "empty.md"
    empty_file.write_text("")

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(empty_file)
    assert entity.observations == []
    assert entity.relations == []


@pytest.mark.asyncio
async def test_missing_sections(tmp_path):
    """Test handling of files with missing sections."""
    content = dedent("""
        ---
        type: test
        id: test/missing
        created: 2024-01-09
        modified: 2024-01-09
        tags: []
        ---
        
        Just some content
        with [[links]] but no sections
        """)

    test_file = tmp_path / "missing.md"
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(test_file)
    assert len(entity.relations) == 1
    assert entity.relations[0].target == "links"
    assert entity.relations[0].type == "links_to"


@pytest.mark.asyncio
async def test_tasks_are_not_observations(tmp_path):
    """Test handling of plain observations without categories."""
    content = dedent("""
        ---
        type: test
        id: test/missing
        created: 2024-01-09
        modified: 2024-01-09
        tags: []
        ---

        - [ ] one
        -[ ] two
        - [x] done
        - [-] not done
        """)

    test_file = tmp_path / "missing.md"
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(test_file)
    assert len(entity.observations) == 0


@pytest.mark.asyncio
async def test_nested_content(tmp_path):
    """Test handling of deeply nested content."""
    content = dedent("""
        ---
        type: test
        id: test/nested
        created: 2024-01-09
        modified: 2024-01-09
        tags: []
        ---
        
        # Test
        
        ## Level 1
        - [test] Level 1 #test (First level)
        - implements [[One]]
            
            ### Level 2
            - [test] Level 2 #test (Second level)
            - uses [[Two]]
                
                #### Level 3
                - [test] Level 3 #test (Third level)
                - needs [[Three]]
        """)

    test_file = tmp_path / "nested.md"
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(test_file)

    # Should find all observations and relations regardless of nesting
    assert len(entity.observations) == 3
    assert len(entity.relations) == 3
    assert {r.target for r in entity.relations} == {"One", "Two", "Three"}


@pytest.mark.asyncio
async def test_malformed_frontmatter(tmp_path):
    """Test handling of malformed frontmatter."""
    # Missing fields
    content = dedent("""
        ---
        type: test
        ---
        
        # Test
        """)

    test_file = tmp_path / "malformed.md"
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity = await parser.parse_file(test_file)
    assert entity.frontmatter.permalink is None


@pytest.mark.asyncio
async def test_file_not_found():
    """Test handling of non-existent files."""
    parser = EntityParser(Path("/tmp"))
    with pytest.raises(FileNotFoundError):
        await parser.parse_file(Path("nonexistent.md"))
