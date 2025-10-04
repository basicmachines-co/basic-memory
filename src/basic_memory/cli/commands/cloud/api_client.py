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

    def __init__(
        self, message: str, status_code: Optional[int] = None, detail: Optional[dict] = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail or {}


class SubscriptionRequiredError(CloudAPIError):
    """Exception raised when user needs an active subscription."""

    def __init__(self, message: str, subscribe_url: str):
        super().__init__(message, status_code=403, detail={"error": "subscription_required"})
        self.subscribe_url = subscribe_url


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
            response = await client.request(method=method, url=url, headers=headers, json=json_data)
            response.raise_for_status()
            return response
        except httpx.HTTPError as e:
            # Check if this is a response error with response details
            if hasattr(e, "response") and e.response is not None:  # pyright: ignore [reportAttributeAccessIssue]
                response = e.response  # type: ignore

                # Try to parse error detail from response
                error_detail = None
                try:
                    error_detail = response.json()
                except Exception:
                    # If JSON parsing fails, we'll handle it as a generic error
                    pass

                # Check for subscription_required error (403)
                if response.status_code == 403 and isinstance(error_detail, dict):
                    # Handle both FastAPI HTTPException format (nested under "detail")
                    # and direct format
                    detail_obj = error_detail.get("detail", error_detail)
                    if (
                        isinstance(detail_obj, dict)
                        and detail_obj.get("error") == "subscription_required"
                    ):
                        message = detail_obj.get("message", "Active subscription required")
                        subscribe_url = detail_obj.get(
                            "subscribe_url", "https://basicmemory.com/subscribe"
                        )
                        raise SubscriptionRequiredError(
                            message=message, subscribe_url=subscribe_url
                        ) from e

                # Raise generic CloudAPIError with status code and detail
                raise CloudAPIError(
                    f"API request failed: {e}",
                    status_code=response.status_code,
                    detail=error_detail if isinstance(error_detail, dict) else {},
                ) from e

            raise CloudAPIError(f"API request failed: {e}") from e
