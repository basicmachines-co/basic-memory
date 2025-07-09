from httpx._types import (
    HeaderTypes,
)
from loguru import logger
from fastmcp.server.dependencies import get_context, get_http_headers, get_http_request


def inject_jwt_header(headers: HeaderTypes | None = None) -> HeaderTypes:
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
    http_request = get_http_request()
    logger.debug(f"HTTP request: {http_request}")

    try:
        # Get JWT from FastMCP context
        context = get_context()
        if hasattr(context, "metadata") and context.metadata:  # type: ignore
            jwt_token = context.metadata.get("jwt_token")  # type: ignore
            if jwt_token:
                headers["Authorization"] = f"Bearer {jwt_token}"  # type: ignore
                logger.debug("Injected JWT token into request headers")
            else:
                logger.debug("No JWT token found in context metadata")
        else:
            logger.debug("No metadata found in FastMCP context")
    except RuntimeError:
        # No active context - this is normal for local development
        logger.debug("No active FastMCP context - skipping JWT injection")
    except Exception as e:
        # Don't fail the request if JWT injection fails
        logger.warning(f"Failed to inject JWT from context: {e}")

    return headers
