from pathlib import Path

import pytest

from scripts.validate_skills import parse_frontmatter


def test_parse_frontmatter_rejects_unquoted_mapping_colon(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "\n".join(
            [
                "---",
                "name: bm-qa",
                "description: Use when validating fixes. Drives the full loop: map issue to commit.",
                "---",
                "# Skill",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="invalid YAML"):
        parse_frontmatter(skill)


def test_parse_frontmatter_allows_url_colons_in_plain_values(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "\n".join(
            [
                "---",
                "name: memory-notes",
                "description: See https://docs.basicmemory.com for usage.",
                "---",
                "# Skill",
                "",
            ]
        ),
        encoding="utf-8",
    )

    frontmatter = parse_frontmatter(skill)

    assert frontmatter["description"] == "See https://docs.basicmemory.com for usage."


def test_parse_frontmatter_strips_matching_single_quotes(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "\n".join(
            [
                "---",
                "name: memory-notes",
                "description: 'Use when values contain mapping-like text: safely.'",
                "---",
                "# Skill",
                "",
            ]
        ),
        encoding="utf-8",
    )

    frontmatter = parse_frontmatter(skill)

    assert frontmatter["description"] == "Use when values contain mapping-like text: safely."


def test_parse_frontmatter_keeps_nested_fields_nested(tmp_path: Path) -> None:
    schema = tmp_path / "schema.md"
    schema.write_text(
        "\n".join(
            [
                "---",
                "type: schema",
                "entity: Task",
                "schema:",
                "  type: object",
                "---",
                "# Task",
                "",
            ]
        ),
        encoding="utf-8",
    )

    frontmatter = parse_frontmatter(schema)

    assert frontmatter["type"] == "schema"
    assert frontmatter["entity"] == "Task"
    assert frontmatter["schema"] == ""
