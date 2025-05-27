"""
Basic Memory FastMCP server.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.utilities.logging import configure_logging as mcp_configure_logging
from mcp.server.auth.settings import AuthSettings

from basic_memory.config import app_config
from basic_memory.services.initialization import initialize_app
from basic_memory.mcp.auth_provider import BasicMemoryOAuthProvider
from basic_memory.mcp.project_session import session
from basic_memory.mcp.external_auth_provider import (
    create_github_provider,
    create_google_provider,
)
from basic_memory.mcp.supabase_auth_provider import SupabaseOAuthProvider

# mcp console logging
mcp_configure_logging(level="ERROR")

load_dotenv()


@dataclass
class AppContext:
    watch_task: Optional[asyncio.Task]


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:  # pragma: no cover
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    watch_task = await initialize_app(app_config)

    # Initialize project session with default project
    session.initialize(app_config.default_project)

    try:
        yield AppContext(watch_task=watch_task)
    finally:
        # Cleanup on shutdown
        if watch_task:
            watch_task.cancel()


# OAuth configuration function
def create_auth_config() -> tuple[AuthSettings | None, Any | None]:
    """Create OAuth configuration if enabled."""
    # Check if OAuth is enabled via environment variable
    import os

    if os.getenv("FASTMCP_AUTH_ENABLED", "false").lower() == "true":
        from pydantic import AnyHttpUrl

        # Configure OAuth settings
        issuer_url = os.getenv("FASTMCP_AUTH_ISSUER_URL", "http://localhost:8000")
        required_scopes = os.getenv("FASTMCP_AUTH_REQUIRED_SCOPES", "read,write")
        docs_url = os.getenv("FASTMCP_AUTH_DOCS_URL") or "http://localhost:8000/docs/oauth"

        auth_settings = AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            service_documentation_url=AnyHttpUrl(docs_url),
            required_scopes=required_scopes.split(",") if required_scopes else ["read", "write"],
        )

        # Create OAuth provider based on type
        provider_type = os.getenv("FASTMCP_AUTH_PROVIDER", "basic").lower()

        if provider_type == "github":
            auth_provider = create_github_provider()
        elif provider_type == "google":
            auth_provider = create_google_provider()
        elif provider_type == "supabase":
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
            supabase_service_key = os.getenv("SUPABASE_SERVICE_KEY")

            if not supabase_url or not supabase_anon_key:
                raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set for Supabase auth")

            auth_provider = SupabaseOAuthProvider(
                supabase_url=supabase_url,
                supabase_anon_key=supabase_anon_key,
                supabase_service_key=supabase_service_key,
                issuer_url=issuer_url,
            )
        else:  # default to "basic"
            auth_provider = BasicMemoryOAuthProvider(issuer_url=issuer_url)

        return auth_settings, auth_provider

    return None, None


# Create auth configuration
auth_settings, auth_provider = create_auth_config()

# Create the shared server instance
mcp = FastMCP(
    name="Basic Memory",
    log_level="DEBUG",
    auth_server_provider=auth_provider,
    auth=auth_settings,
)
