"""Cloud commands package."""

from basic_memory.cli.app import cloud_app

# Import all commands to register them with typer
from basic_memory.cli.commands.cloud.core_commands import *  # noqa: F401,F403
from basic_memory.cli.commands.cloud.api_client import get_authenticated_headers, get_cloud_config  # noqa: F401
from basic_memory.cli.commands.cloud.upload_command import *  # noqa: F401,F403

# Register snapshot sub-command group
from basic_memory.cli.commands.cloud.snapshot import snapshot_app

cloud_app.add_typer(snapshot_app, name="snapshot")

# Register restore command (directly on cloud_app via decorator)
from basic_memory.cli.commands.cloud.restore import restore  # noqa: F401, E402
