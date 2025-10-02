"""Cloud API client utilities."""

from typing import Optional

import httpx
import typer
from rich.console import Console

from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ConfigManager

console = Console()


class CloudAPIError(Exception):
    """Exception raised for cloud API errors."""

    pass


def get_cloud_config() -> tuple[str, str, str]:
    """Get cloud OAuth configuration from config."""
    config_manager = ConfigManager()
    config = config_manager.config
    return config.cloud_client_id, config.cloud_domain, config.cloud_host


async def get_authenticated_headers() -> dict[str, str]:
    """
    Get authentication headers with JWT token.
    handles jwt refresh if needed.
    """
    client_id, domain, _ = get_cloud_config()
    auth = CLIAuth(client_id=client_id, authkit_domain=domain)
    token = await auth.get_valid_token()
    if not token:
        console.print("[red]Not authenticated. Please run 'basic-memory cloud login' first.[/red]")
        raise typer.Exit(1)

    return {"Authorization": f"Bearer {token}"}


async def make_api_request(
    method: str,
    url: str,
    headers: Optional[dict] = None,
    json_data: Optional[dict] = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Make an API request to the cloud service."""
    headers = headers or {}
    auth_headers = await get_authenticated_headers()
    headers.update(auth_headers)
    # Add debug headers to help with compression issues
    headers.setdefault("Accept-Encoding", "identity")  # Disable compression for debugging

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            console.print(f"[dim]Making {method} request to {url}[/dim]")
            # console.print(f"[dim]Headers: {dict(headers)}[/dim]")

            response = await client.request(method=method, url=url, headers=headers, json=json_data)

            console.print(f"[dim]Response status: {response.status_code}[/dim]")
            # console.print(f"[dim]Response headers: {dict(response.headers)}[/dim]")

            response.raise_for_status()
            return response
        except httpx.HTTPError as e:
            console.print(f"[red]HTTP Error details: {e}[/red]")
            # Check if this is a response error with response details
            if hasattr(e, "response") and e.response is not None:  # pyright: ignore [reportAttributeAccessIssue]
                response = e.response  # type: ignore
                console.print(f"[red]Response status: {response.status_code}[/red]")
                console.print(f"[red]Response headers: {dict(response.headers)}[/red]")
                try:
                    console.print(f"[red]Response text: {response.text}[/red]")
                except Exception:
                    console.print("[red]Could not read response text[/red]")
            raise CloudAPIError(f"API request failed: {e}") from e
