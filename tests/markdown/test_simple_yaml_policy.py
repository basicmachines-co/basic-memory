"""Note YAML is a tree: aliases and merge keys are unsupported (#1892)."""

from pathlib import Path

import frontmatter
import pytest
import yaml

from basic_memory.file_utils import ParseError, dump_frontmatter, parse_frontmatter
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.mcp.note_reads import parse_opening_frontmatter
from basic_memory.okf.validation import parse_document


GRAPH_YAML = [
    "tags: &shared [one, two]\nother: *shared\n",
    "tags: &cycle [*cycle]\n",
    "metadata:\n  <<: {status: private}\n",
    "metadata: {<<: [{status: one}, {status: two}]}\n",
]


@pytest.mark.parametrize("metadata", GRAPH_YAML, ids=["alias", "cycle", "merge", "merge-list"])
def test_core_frontmatter_refuses_yaml_graph_syntax(metadata: str):
    content = f"---\n{metadata}---\nBody\n"
    with pytest.raises(ParseError, match="aliases|merge"):
        parse_frontmatter(content)


@pytest.mark.parametrize("metadata", GRAPH_YAML, ids=["alias", "cycle", "merge", "merge-list"])
def test_okf_frontmatter_refuses_yaml_graph_syntax(metadata: str):
    with pytest.raises(ValueError, match="aliases|merge"):
        parse_document(f"---\n{metadata}---\nBody\n")


@pytest.mark.parametrize("metadata", GRAPH_YAML, ids=["alias", "cycle", "merge", "merge-list"])
def test_read_frontmatter_preserves_unsupported_yaml_as_body(metadata: str):
    content = f"---\n{metadata}---\nBody\n"
    body, parsed = parse_opening_frontmatter(content)
    assert body == content
    assert parsed is None


@pytest.mark.asyncio
async def test_entity_parser_marks_cyclic_frontmatter_malformed(tmp_path: Path):
    content = "---\ntags: &cycle [*cycle]\n---\nBody\n"
    parsed = await EntityParser(base_path=tmp_path).parse_markdown_content(
        tmp_path / "note.md", content
    )
    assert parsed.frontmatter_state == "malformed"
    assert parsed.content is not None
    assert "tags: &cycle [*cycle]" in parsed.content


def test_literal_yaml_markers_remain_plain_data():
    metadata = parse_frontmatter('---\n"<<": literal\ntext: "*not-an-alias &not-an-anchor"\n---\n')
    assert metadata == {"<<": "literal", "text": "*not-an-alias &not-an-anchor"}


def test_frontmatter_writer_does_not_emit_aliases_for_shared_values():
    shared = ["one", "two"]
    post = frontmatter.Post("Body")
    post.metadata.update({"tags": shared, "other": shared})
    rendered = dump_frontmatter(post)
    assert not any(
        isinstance(token, yaml.AliasToken) for token in yaml.scan(rendered.split("---")[1])
    )
    assert parse_frontmatter(rendered) == {"tags": shared, "other": shared}
