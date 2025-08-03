from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp.server.dependencies import get_context, get_http_headers, get_http_request


def inject_auth_header(headers: HeaderTypes | None = None) -> HeaderTypes:
    """
    Inject JWT token from FastMCP context into headers if available.

    Args:
        headers: Existing headers dict or None

    Returns:
        Headers dict with Authorization header added if JWT is available
    """
    # Start with existing headers or empty dict
    if headers is None:
        headers = {}
    elif not isinstance(headers, dict):
        # Convert other header types to dict
        headers = dict(headers)  # type: ignore
    else:
        # Make a copy to avoid modifying the original
        headers = headers.copy()

    http_headers = get_http_headers()
    logger.debug(f"HTTP headers: {http_headers}")

    authorization = http_headers.get("Authorization") or http_headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization # type: ignore
        logger.debug("Injected JWT token into authorization request headers")
    else:
        logger.debug("No authorization found in request headers")

    return headers


def get_base_url_from_headers() -> str | None:
    """Extract base URL from x-bm-tenant-app-name header.
    
    Returns:
        Base URL for the tenant API, or None if header not found
    """
    try:
        http_headers = get_http_headers()
        tenant_app_name = http_headers.get("x-bm-tenant-app-name")
        
        if tenant_app_name:
            # Construct the fly.io app URL
            base_url = f"https://{tenant_app_name}.fly.dev"
            logger.debug(f"Using dynamic base URL: {base_url}")
            return base_url
        else:
            logger.debug("No x-bm-tenant-app-name header found")
            return None
    except Exception as e:
        logger.warning(f"Failed to extract base URL from headers: {e}")
        return None
