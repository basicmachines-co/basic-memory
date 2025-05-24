"""Template loading and rendering utilities for the Basic Memory API.

This module handles the loading and rendering of Handlebars templates from the
templates directory, providing a consistent interface for all prompt-related
formatting needs.
"""

import textwrap
from typing import Dict, Any, Optional, Callable
from pathlib import Path
import json
import datetime

import pybars
from loguru import logger

# Get the base path of the templates directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


# Custom helpers for Handlebars
def _date_helper(this, *args):
    """Format a date using the given format string."""
    if len(args) < 1:  # pragma: no cover
        return ""

    timestamp = args[0]
    format_str = args[1] if len(args) > 1 else "%Y-%m-%d %H:%M"

    if hasattr(timestamp, "strftime"):
        result = timestamp.strftime(format_str)
    elif isinstance(timestamp, str):
        try:
            dt = datetime.datetime.fromisoformat(timestamp)
            result = dt.strftime(format_str)
        except ValueError:
            result = timestamp
    else:
        result = str(timestamp)  # pragma: no cover

    return pybars.strlist([result])


def _default_helper(this, *args):
    """Return a default value if the given value is None or empty."""
    if len(args) < 2:  # pragma: no cover
        return ""

    value = args[0]
    default_value = args[1]

    result = default_value if value is None or value == "" else value
    # Use strlist for consistent handling of HTML escaping
    return pybars.strlist([str(result)])


def _capitalize_helper(this, *args):
    """Capitalize the first letter of a string."""
    if len(args) < 1:  # pragma: no cover
        return ""

    text = args[0]
    if not text or not isinstance(text, str):  # pragma: no cover
        result = ""
    else:
        result = text.capitalize()

    return pybars.strlist([result])


def _round_helper(this, *args):
    """Round a number to the specified number of decimal places."""
    if len(args) < 1:
        return ""

    value = args[0]
    decimal_places = args[1] if len(args) > 1 else 2

    try:
        result = str(round(float(value), int(decimal_places)))
    except (ValueError, TypeError):
        result = str(value)

    return pybars.strlist([result])


def _size_helper(this, *args):
    """Return the size/length of a collection."""
    if len(args) < 1:
        return 0

    value = args[0]
    if value is None:
        result = "0"
    elif isinstance(value, (list, tuple, dict, str)):
        result = str(len(value))  # pragma: no cover
    else:  # pragma: no cover
        result = "0"

    return pybars.strlist([result])


def _json_helper(this, *args):
    """Convert a value to a JSON string."""
    if len(args) < 1:  # pragma: no cover
        return "{}"

    value = args[0]
    # For pybars, we need to return a SafeString to prevent HTML escaping
    result = json.dumps(value)  # pragma: no cover
    # Safe string implementation to prevent HTML escaping
    return pybars.strlist([result])


def _math_helper(this, *args):
    """Perform basic math operations."""
    if len(args) < 3:
        return pybars.strlist(["Math error: Insufficient arguments"])

    lhs = args[0]
    operator = args[1]
    rhs = args[2]

    try:
        lhs = float(lhs)
        rhs = float(rhs)
        if operator == "+":
            result = str(lhs + rhs)
        elif operator == "-":
            result = str(lhs - rhs)
        elif operator == "*":
            result = str(lhs * rhs)
        elif operator == "/":
            result = str(lhs / rhs)
        else:
            result = f"Unsupported operator: {operator}"
    except (ValueError, TypeError) as e:
        result = f"Math error: {e}"

    return pybars.strlist([result])


def _lt_helper(this, *args):
    """Check if left hand side is less than right hand side."""
    if len(args) < 2:
        return False

    lhs = args[0]
    rhs = args[1]

    try:
        return float(lhs) < float(rhs)
    except (ValueError, TypeError):
        # Fall back to string comparison for non-numeric values
        return str(lhs) < str(rhs)


def _if_cond_helper(this, options, condition):
    """Block helper for custom if conditionals."""
    if condition:
        return options["fn"](this)
    elif "inverse" in options:
        return options["inverse"](this)
    return ""  # pragma: no cover


def _dedent_helper(this, options):
    """Dedent a block of text to remove common leading whitespace.

    Usage:
    {{#dedent}}
        This text will have its
        common leading whitespace removed
        while preserving relative indentation.
    {{/dedent}}
    """
    if "fn" not in options:  # pragma: no cover
        return ""

    # Get the content from the block
    content = options["fn"](this)

    # Convert to string if it's a strlist
    if (
        isinstance(content, list)
        or hasattr(content, "__iter__")
        and not isinstance(content, (str, bytes))
    ):
        content_str = "".join(str(item) for item in content)  # pragma: no cover
    else:
        content_str = str(content)  # pragma: no cover

    # Add trailing and leading newlines to ensure proper dedenting
    # This is critical for textwrap.dedent to work correctly with mixed content
    content_str = "\n" + content_str + "\n"

    # Use textwrap to dedent the content and remove the extra newlines we added
    dedented = textwrap.dedent(content_str)[1:-1]

    # Return as a SafeString to prevent HTML escaping
    return pybars.strlist([dedented])  # pragma: no cover


class TemplateLoader:
    """Loader for Handlebars templates.

    This class is responsible for loading templates from disk and rendering
    them with the provided context data.
    """

    def __init__(self, template_dir: Optional[str] = None):
        """Initialize the template loader.

        Args:
            template_dir: Optional custom template directory path
        """
        self.template_dir = Path(template_dir) if template_dir else TEMPLATES_DIR
        self.template_cache: Dict[str, Callable] = {}
        self.compiler = pybars.Compiler()

        # Set up standard helpers
        self.helpers = {
            "date": _date_helper,
            "default": _default_helper,
            "capitalize": _capitalize_helper,
            "round": _round_helper,
            "size": _size_helper,
            "json": _json_helper,
            "math": _math_helper,
            "lt": _lt_helper,
            "if_cond": _if_cond_helper,
            "dedent": _dedent_helper,
        }

        logger.debug(f"Initialized template loader with directory: {self.template_dir}")

    def get_template(self, template_path: str) -> Callable:
        """Get a template by path, using cache if available.

        Args:
            template_path: The path to the template, relative to the templates directory

        Returns:
            The compiled Handlebars template

        Raises:
            FileNotFoundError: If the template doesn't exist
        """
        if template_path in self.template_cache:
            return self.template_cache[template_path]

        # Convert from Liquid-style path to Handlebars extension
        if template_path.endswith(".liquid"):
            template_path = template_path.replace(".liquid", ".hbs")
        elif not template_path.endswith(".hbs"):
            template_path = f"{template_path}.hbs"

        full_path = self.template_dir / template_path

        if not full_path.exists():
            raise FileNotFoundError(f"Template not found: {full_path}")

        with open(full_path, "r", encoding="utf-8") as f:
            template_str = f.read()

        template = self.compiler.compile(template_str)
        self.template_cache[template_path] = template

        logger.debug(f"Loaded template: {template_path}")
        return template

    async def render(self, template_path: str, context: Dict[str, Any]) -> str:
        """Render a template with the given context.

        Args:
            template_path: The path to the template, relative to the templates directory
            context: The context data to pass to the template

        Returns:
            The rendered template as a string
        """
        template = self.get_template(template_path)
        return template(context, helpers=self.helpers)

    def clear_cache(self) -> None:
        """Clear the template cache."""
        self.template_cache.clear()
        logger.debug("Template cache cleared")


# Global template loader instance
template_loader = TemplateLoader()
