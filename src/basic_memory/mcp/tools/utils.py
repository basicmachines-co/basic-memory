"""Utility functions for making HTTP requests in Basic Memory MCP tools.

These functions provide a consistent interface for making HTTP requests
to the Basic Memory API, with improved error handling and logging.
"""

import typing
from typing import Optional

from httpx import Response, URL, AsyncClient, HTTPStatusError
from httpx._client import UseClientDefault, USE_CLIENT_DEFAULT
from httpx._types import (
    RequestContent,
    RequestData,
    RequestFiles,
    QueryParamTypes,
    HeaderTypes,
    CookieTypes,
    AuthTypes,
    TimeoutTypes,
    RequestExtensions,
)
from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.config import ConfigManager


def get_error_message(
    status_code: int, url: URL | str, method: str, msg: Optional[str] = None
) -> str:
    """Get a friendly error message based on the HTTP status code.

    Args:
        status_code: The HTTP status code
        url: The URL that was requested
        method: The HTTP method used

    Returns:
        A user-friendly error message
    """
    # Extract path from URL for cleaner error messages
    if isinstance(url, str):
        path = url.split("/")[-1]
    else:
        path = str(url).split("/")[-1] if url else "resource"

    # Client errors (400-499)
    if status_code == 400:
        return f"Invalid request: The request to '{path}' was malformed or invalid"
    elif status_code == 401:  # pragma: no cover
        return f"Authentication required: You need to authenticate to access '{path}'"
    elif status_code == 403:  # pragma: no cover
        return f"Access denied: You don't have permission to access '{path}'"
    elif status_code == 404:
        return f"Resource not found: '{path}' doesn't exist or has been moved"
    elif status_code == 409:  # pragma: no cover
        return f"Conflict: The request for '{path}' conflicts with the current state"
    elif status_code == 429:  # pragma: no cover
        return "Too many requests: Please slow down and try again later"
    elif 400 <= status_code < 500:  # pragma: no cover
        return f"Client error ({status_code}): The request for '{path}' could not be completed"

    # Server errors (500-599)
    elif status_code == 500:
        return f"Internal server error: Something went wrong processing '{path}'"
    elif status_code == 503:  # pragma: no cover
        return (
            f"Service unavailable: The server is currently unable to handle requests for '{path}'"
        )
    elif 500 <= status_code < 600:  # pragma: no cover
        return f"Server error ({status_code}): The server encountered an error handling '{path}'"

    # Fallback for any other status code
    else:  # pragma: no cover
        return f"HTTP error {status_code}: {method} request to '{path}' failed"


def _extract_response_data(response: Response) -> typing.Any:
    """Safely decode response payload for error reporting."""
    try:
        return response.json()
    except Exception:
        return None


def _response_detail_text(response_data: typing.Any) -> str | None:
    """Extract textual error detail from API payloads."""
    if isinstance(response_data, dict):
        detail = response_data.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict):
            nested_message = detail.get("message")
            if isinstance(nested_message, str):
                return nested_message
            return str(detail)
        if detail is not None:
            return str(detail)
    return None


def _has_configured_cloud_api_key() -> bool:
    """Check whether a cloud API key is currently configured."""
    try:
        return bool(ConfigManager().config.cloud_api_key)
    except Exception:
        return False


def _resolve_error_message(
    status_code: int, url: URL | str, method: str, response_data: typing.Any
) -> str:
    """Resolve a user-facing error message with cloud auth remediation when relevant."""
    detail_text = _response_detail_text(response_data)

    if status_code == 401 and _has_configured_cloud_api_key():
        detail_lower = detail_text.lower() if detail_text else ""
        if (
            "invalid jwt" in detail_lower
            or "invalid token" in detail_lower
            or "authentication required" in detail_lower
            or not detail_lower
        ):
            return (
                "Authentication failed: the configured cloud API key was rejected by the server. "
                "Basic Memory prioritizes cloud_api_key over OAuth for cloud routing. "
                "Fix by running `bm cloud api-key save <valid-key>` "
                "or remove `cloud_api_key` and use `bm cloud login`."
            )

    if detail_text:
        return detail_text

    return get_error_message(status_code, url, method)


async def call_get(
    client: AsyncClient,
    url: URL | str,
    *,
    params: QueryParamTypes | None = None,
    headers: HeaderTypes | None = None,
    cookies: CookieTypes | None = None,
    auth: AuthTypes | UseClientDefault | None = USE_CLIENT_DEFAULT,
    follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    extensions: RequestExtensions | None = None,
) -> Response:
    """Make a GET request and handle errors appropriately.

    Args:
        client: The HTTPX AsyncClient to use
        url: The URL to request
        params: Query parameters
        headers: HTTP headers
        cookies: HTTP cookies
        auth: Authentication
        follow_redirects: Whether to follow redirects
        timeout: Request timeout
        extensions: HTTPX extensions

    Returns:
        The HTTP response

    Raises:
        ToolError: If the request fails with an appropriate error message
    """
    logger.debug(f"Calling GET '{url}' params: '{params}'")
    error_message = None

    try:
        response = await client.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(status_code, url, "GET", response_data)

        # Log at appropriate level based on status code
        if 400 <= status_code < 500:
            # Client errors: log as info except for 429 (Too Many Requests)
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: GET {url}: {error_message}")
            else:
                logger.info(f"Client error: GET {url}: {error_message}")
        else:  # pragma: no cover
            # Server errors: log as error
            logger.error(f"Server error: GET {url}: {error_message}")

        # Raise a tool error with the friendly message
        response.raise_for_status()  # Will always raise since we're in the error case
        return response  # This line will never execute, but it satisfies the type checker  # pragma: no cover

    except HTTPStatusError as e:
        raise ToolError(error_message) from e


async def call_put(
    client: AsyncClient,
    url: URL | str,
    *,
    content: RequestContent | None = None,
    data: RequestData | None = None,
    files: RequestFiles | None = None,
    json: typing.Any | None = None,
    params: QueryParamTypes | None = None,
    headers: HeaderTypes | None = None,
    cookies: CookieTypes | None = None,
    auth: AuthTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    extensions: RequestExtensions | None = None,
) -> Response:
    """Make a PUT request and handle errors appropriately.

    Args:
        client: The HTTPX AsyncClient to use
        url: The URL to request
        content: Request content
        data: Form data
        files: Files to upload
        json: JSON data
        params: Query parameters
        headers: HTTP headers
        cookies: HTTP cookies
        auth: Authentication
        follow_redirects: Whether to follow redirects
        timeout: Request timeout
        extensions: HTTPX extensions

    Returns:
        The HTTP response

    Raises:
        ToolError: If the request fails with an appropriate error message
    """
    logger.debug(f"Calling PUT '{url}'")
    error_message = None

    try:
        response = await client.put(
            url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code

        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(status_code, url, "PUT", response_data)

        # Log at appropriate level based on status code
        if 400 <= status_code < 500:
            # Client errors: log as info except for 429 (Too Many Requests)
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: PUT {url}: {error_message}")
            else:
                logger.info(f"Client error: PUT {url}: {error_message}")
        else:  # pragma: no cover
            # Server errors: log as error
            logger.error(f"Server error: PUT {url}: {error_message}")

        # Raise a tool error with the friendly message
        response.raise_for_status()  # Will always raise since we're in the error case
        return response  # This line will never execute, but it satisfies the type checker  # pragma: no cover

    except HTTPStatusError as e:
        raise ToolError(error_message) from e


async def call_patch(
    client: AsyncClient,
    url: URL | str,
    *,
    content: RequestContent | None = None,
    data: RequestData | None = None,
    files: RequestFiles | None = None,
    json: typing.Any | None = None,
    params: QueryParamTypes | None = None,
    headers: HeaderTypes | None = None,
    cookies: CookieTypes | None = None,
    auth: AuthTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    extensions: RequestExtensions | None = None,
) -> Response:
    """Make a PATCH request and handle errors appropriately.

    Args:
        client: The HTTPX AsyncClient to use
        url: The URL to request
        content: Request content
        data: Form data
        files: Files to upload
        json: JSON data
        params: Query parameters
        headers: HTTP headers
        cookies: HTTP cookies
        auth: Authentication
        follow_redirects: Whether to follow redirects
        timeout: Request timeout
        extensions: HTTPX extensions

    Returns:
        The HTTP response

    Raises:
        ToolError: If the request fails with an appropriate error message
    """
    logger.debug(f"Calling PATCH '{url}'")

    try:
        response = await client.patch(
            url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code

        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(status_code, url, "PATCH", response_data)

        # Log at appropriate level based on status code
        if 400 <= status_code < 500:
            # Client errors: log as info except for 429 (Too Many Requests)
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: PATCH {url}: {error_message}")
            else:
                logger.info(f"Client error: PATCH {url}: {error_message}")
        else:  # pragma: no cover
            # Server errors: log as error
            logger.error(f"Server error: PATCH {url}: {error_message}")  # pragma: no cover

        # Raise a tool error with the friendly message
        response.raise_for_status()  # Will always raise since we're in the error case
        return response  # This line will never execute, but it satisfies the type checker  # pragma: no cover

    except HTTPStatusError as e:
        status_code = e.response.status_code

        response_data = _extract_response_data(e.response)
        error_message = _resolve_error_message(status_code, url, "PATCH", response_data)

        raise ToolError(error_message) from e


async def call_post(
    client: AsyncClient,
    url: URL | str,
    *,
    content: RequestContent | None = None,
    data: RequestData | None = None,
    files: RequestFiles | None = None,
    json: typing.Any | None = None,
    params: QueryParamTypes | None = None,
    headers: HeaderTypes | None = None,
    cookies: CookieTypes | None = None,
    auth: AuthTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    extensions: RequestExtensions | None = None,
) -> Response:
    """Make a POST request and handle errors appropriately.

    Args:
        client: The HTTPX AsyncClient to use
        url: The URL to request
        content: Request content
        data: Form data
        files: Files to upload
        json: JSON data
        params: Query parameters
        headers: HTTP headers
        cookies: HTTP cookies
        auth: Authentication
        follow_redirects: Whether to follow redirects
        timeout: Request timeout
        extensions: HTTPX extensions

    Returns:
        The HTTP response

    Raises:
        ToolError: If the request fails with an appropriate error message
    """
    logger.debug(f"Calling POST '{url}'")
    error_message = None

    try:
        response = await client.post(
            url=url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )
        logger.debug(f"response: {response.json()}")

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(status_code, url, "POST", response_data)

        # Log at appropriate level based on status code
        if 400 <= status_code < 500:
            # Client errors: log as info except for 429 (Too Many Requests)
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: POST {url}: {error_message}")
            else:  # pragma: no cover
                logger.info(f"Client error: POST {url}: {error_message}")
        else:
            # Server errors: log as error
            logger.error(f"Server error: POST {url}: {error_message}")

        # Raise a tool error with the friendly message
        response.raise_for_status()  # Will always raise since we're in the error case
        return response  # This line will never execute, but it satisfies the type checker  # pragma: no cover

    except HTTPStatusError as e:
        raise ToolError(error_message) from e


async def resolve_entity_id(client: AsyncClient, project_external_id: str, identifier: str) -> str:
    """Resolve a string identifier to an entity external_id using the v2 API.

    Args:
        client: HTTP client for API calls
        project_external_id: Project external ID (UUID)
        identifier: The identifier to resolve (permalink, title, or path)

    Returns:
        The resolved entity external_id (UUID)

    Raises:
        ToolError: If the identifier cannot be resolved
    """
    try:
        response = await call_post(
            client,
            f"/v2/projects/{project_external_id}/knowledge/resolve",
            json={"identifier": identifier},
        )
        data = response.json()
        return data["external_id"]
    except HTTPStatusError as e:
        if e.response.status_code == 404:  # pragma: no cover
            raise ToolError(f"Entity not found: '{identifier}'")  # pragma: no cover
        raise ToolError(f"Error resolving identifier '{identifier}': {e}")  # pragma: no cover
    except Exception as e:
        raise ToolError(
            f"Unexpected error resolving identifier '{identifier}': {e}"
        )  # pragma: no cover


async def call_delete(
    client: AsyncClient,
    url: URL | str,
    *,
    params: QueryParamTypes | None = None,
    headers: HeaderTypes | None = None,
    cookies: CookieTypes | None = None,
    auth: AuthTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
    extensions: RequestExtensions | None = None,
) -> Response:
    """Make a DELETE request and handle errors appropriately.

    Args:
        client: The HTTPX AsyncClient to use
        url: The URL to request
        params: Query parameters
        headers: HTTP headers
        cookies: HTTP cookies
        auth: Authentication
        follow_redirects: Whether to follow redirects
        timeout: Request timeout
        extensions: HTTPX extensions

    Returns:
        The HTTP response

    Raises:
        ToolError: If the request fails with an appropriate error message
    """
    logger.debug(f"Calling DELETE '{url}'")
    error_message = None

    try:
        response = await client.delete(
            url=url,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(status_code, url, "DELETE", response_data)

        # Log at appropriate level based on status code
        if 400 <= status_code < 500:
            # Client errors: log as info except for 429 (Too Many Requests)
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: DELETE {url}: {error_message}")
            else:
                logger.info(f"Client error: DELETE {url}: {error_message}")
        else:  # pragma: no cover
            # Server errors: log as error
            logger.error(f"Server error: DELETE {url}: {error_message}")

        # Raise a tool error with the friendly message
        response.raise_for_status()  # Will always raise since we're in the error case
        return response  # This line will never execute, but it satisfies the type checker  # pragma: no cover

    except HTTPStatusError as e:
        raise ToolError(error_message) from e
