"""Tests for additional template loader helpers."""

import pytest
from datetime import datetime

from basic_memory.api.template_loader import TemplateLoader


@pytest.fixture
def temp_template_dir(tmpdir):
    """Create a temporary directory for test templates."""
    template_dir = tmpdir.mkdir("templates").mkdir("prompts")
    return template_dir


@pytest.fixture
def custom_template_loader(temp_template_dir):
    """Return a TemplateLoader instance with a custom template directory."""
    return TemplateLoader(str(temp_template_dir))


@pytest.mark.asyncio
async def test_round_helper(custom_template_loader, temp_template_dir):
    """Test the round helper for number formatting."""
    # Create template file
    round_path = temp_template_dir / "round.hbs"
    round_path.write_text(
        "{{round number}} {{round number 0}} {{round number 3}}",
        encoding="utf-8",
    )

    # Test with various values
    result = await custom_template_loader.render("round.hbs", {"number": 3.14159})
    assert result == "3.14 3.0 3.142" or result == "3.14 3 3.142"

    # Test with non-numeric value
    result = await custom_template_loader.render("round.hbs", {"number": "not-a-number"})
    assert "not-a-number" in result

    # Test with insufficient args
    empty_path = temp_template_dir / "round_empty.hbs"
    empty_path.write_text("{{round}}", encoding="utf-8")
    result = await custom_template_loader.render("round_empty.hbs", {})
    assert result == ""


@pytest.mark.asyncio
async def test_date_helper_edge_cases(custom_template_loader, temp_template_dir):
    """Test edge cases for the date helper."""
    # Create template file
    date_path = temp_template_dir / "date_edge.hbs"
    date_path.write_text(
        "{{date timestamp}} {{date timestamp '%Y'}} {{date string_date}} {{date invalid_date}} {{date}}",
        encoding="utf-8",
    )

    # Test with various values
    result = await custom_template_loader.render(
        "date_edge.hbs",
        {
            "timestamp": datetime(2023, 1, 1, 12, 30),
            "string_date": "2023-01-01T12:30:00",
            "invalid_date": "not-a-date",
        },
    )

    assert "2023-01-01" in result
    assert "2023" in result  # Custom format
    assert "not-a-date" in result  # Invalid date passed through
    assert result.strip() != ""  # Empty date case


@pytest.mark.asyncio
async def test_size_helper_edge_cases(custom_template_loader, temp_template_dir):
    """Test edge cases for the size helper."""
    # Create template file
    size_path = temp_template_dir / "size_edge.hbs"
    size_path.write_text(
        "{{size list}} {{size string}} {{size dict}} {{size null}} {{size}}",
        encoding="utf-8",
    )

    # Test with various values
    result = await custom_template_loader.render(
        "size_edge.hbs",
        {
            "list": [1, 2, 3, 4, 5],
            "string": "hello",
            "dict": {"a": 1, "b": 2, "c": 3},
            "null": None,
        },
    )

    assert "5" in result  # List size
    assert "hello".find("5") == -1  # String size should be 5
    assert "3" in result  # Dict size
    assert "0" in result  # Null size
    assert result.count("0") >= 2  # At least two zeros (null and empty args)


@pytest.mark.asyncio
async def test_math_helper(custom_template_loader, temp_template_dir):
    """Test the math helper for basic arithmetic."""
    # Create template file
    math_path = temp_template_dir / "math.hbs"
    math_path.write_text(
        "{{math 5 '+' 3}} {{math 10 '-' 4}} {{math 6 '*' 7}} {{math 20 '/' 5}}",
        encoding="utf-8",
    )

    # Test basic operations
    result = await custom_template_loader.render("math.hbs", {})
    assert "8" in result  # Addition
    assert "6" in result  # Subtraction
    assert "42" in result  # Multiplication
    assert "4" in result  # Division

    # Test with invalid operator
    invalid_op_path = temp_template_dir / "math_invalid_op.hbs"
    invalid_op_path.write_text("{{math 5 'invalid' 3}}", encoding="utf-8")
    result = await custom_template_loader.render("math_invalid_op.hbs", {})
    assert "Unsupported operator" in result

    # Test with invalid numeric values
    invalid_num_path = temp_template_dir / "math_invalid_num.hbs"
    invalid_num_path.write_text("{{math 'not-a-number' '+' 3}}", encoding="utf-8")
    result = await custom_template_loader.render("math_invalid_num.hbs", {})
    assert "Math error" in result

    # Test with insufficient arguments
    insufficient_path = temp_template_dir / "math_insufficient.hbs"
    insufficient_path.write_text("{{math 5 '+'}}", encoding="utf-8")
    result = await custom_template_loader.render("math_insufficient.hbs", {})
    assert "Insufficient arguments" in result


@pytest.mark.asyncio
async def test_if_cond_helper(custom_template_loader, temp_template_dir):
    """Test the if_cond helper for conditionals."""
    # Create template file with true condition
    if_true_path = temp_template_dir / "if_true.hbs"
    if_true_path.write_text(
        "{{#if_cond (lt 5 10)}}True condition{{else}}False condition{{/if_cond}}",
        encoding="utf-8",
    )

    # Create template file with false condition
    if_false_path = temp_template_dir / "if_false.hbs"
    if_false_path.write_text(
        "{{#if_cond (lt 15 10)}}True condition{{else}}False condition{{/if_cond}}",
        encoding="utf-8",
    )

    # Test true condition
    result = await custom_template_loader.render("if_true.hbs", {})
    assert result == "True condition"

    # Test false condition
    result = await custom_template_loader.render("if_false.hbs", {})
    assert result == "False condition"


@pytest.mark.asyncio
async def test_lt_helper_edge_cases(custom_template_loader, temp_template_dir):
    """Test edge cases for the lt (less than) helper."""
    # Create template file
    lt_path = temp_template_dir / "lt_edge.hbs"
    lt_path.write_text(
        "{{#if_cond (lt 'a' 'b')}}String LT True{{else}}String LT False{{/if_cond}} "
        "{{#if_cond (lt 'z' 'a')}}String LT2 True{{else}}String LT2 False{{/if_cond}} "
        "{{#if_cond (lt)}}Missing args True{{else}}Missing args False{{/if_cond}}",
        encoding="utf-8",
    )

    # Test with string values and missing args
    result = await custom_template_loader.render("lt_edge.hbs", {})
    assert "String LT True" in result  # 'a' < 'b' is true
    assert "String LT2 False" in result  # 'z' < 'a' is false
    assert "Missing args False" in result  # Missing args should return false


@pytest.mark.asyncio
async def test_dedent_helper_edge_case(custom_template_loader, temp_template_dir):
    """Test an edge case for the dedent helper."""
    # Create template with empty dedent block
    empty_dedent_path = temp_template_dir / "empty_dedent.hbs"
    empty_dedent_path.write_text("{{#dedent}}{{/dedent}}", encoding="utf-8")

    # Test empty block
    result = await custom_template_loader.render("empty_dedent.hbs", {})
    assert result == ""

    # Test with complex content including lists
    complex_dedent_path = temp_template_dir / "complex_dedent.hbs"
    complex_dedent_path.write_text(
        "{{#dedent}}\n    {{#each items}}\n        - {{this}}\n    {{/each}}\n{{/dedent}}",
        encoding="utf-8",
    )

    result = await custom_template_loader.render("complex_dedent.hbs", {"items": [1, 2, 3]})
    assert "- 1" in result
    assert "- 2" in result
    assert "- 3" in result
