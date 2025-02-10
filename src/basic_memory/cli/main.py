"""Main CLI entry point for basic-memory."""

from basic_memory.cli.app import app
from basic_memory.utils import setup_logging

# Register commands
from basic_memory.cli.commands import status, sync
__all__ = ["status", "sync"]


# Set up logging when module is imported
setup_logging(log_file=".basic-memory/basic-memory-cli.log")

if __name__ == "__main__":  # pragma: no cover
    app()