"""Shared loader for the bundled cloud-discovery markdown resources."""

from pathlib import Path


def load_discovery_resource(filename: str) -> str:
    """Read a bundled discovery markdown file with promo placeholders rendered.

    The markdown carries a {{OSS_DISCOUNT_CODE}} placeholder so the promo code
    has one source of truth (cli.promo); substitute before it reaches users.
    """
    # Import here to avoid pulling CLI promo machinery (analytics, rich, config)
    # into the MCP server import graph at module load.
    from basic_memory.cli.promo import OSS_DISCOUNT_CODE

    content = (Path(__file__).parent / filename).read_text(encoding="utf-8")
    return content.replace("{{OSS_DISCOUNT_CODE}}", OSS_DISCOUNT_CODE)
