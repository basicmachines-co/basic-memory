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
        # get the message if available
        response_data = response.json()
        if isinstance(response_data, dict) and "detail" in response_data:
            error_message = response_data["detail"]
        else:
            error_message = get_error_message(status_code, url, "PUT")

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

        # get the message if available
        response_data = response.json()
        if isinstance(response_data, dict) and "detail" in response_data:
            error_message = response_data["detail"]  # pragma: no cover
        else:
            error_message = get_error_message(status_code, url, "PUT")

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

        # Try to extract specific error message from response body
        try:
            response_data = response.json()
            if isinstance(response_data, dict) and "detail" in response_data:
                error_message = response_data["detail"]
            else:
                error_message = get_error_message(status_code, url, "PATCH")  # pragma: no cover
        except Exception:  # pragma: no cover
            error_message = get_error_message(status_code, url, "PATCH")  # pragma: no cover

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

        # Try to extract specific error message from response body
        try:
            response_data = e.response.json()
            if isinstance(response_data, dict) and "detail" in response_data:
                error_message = response_data["detail"]
            else:
                error_message = get_error_message(status_code, url, "PATCH")  # pragma: no cover
        except Exception:  # pragma: no cover
            error_message = get_error_message(status_code, url, "PATCH")  # pragma: no cover

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
        # get the message if available
        response_data = response.json()
        if isinstance(response_data, dict) and "detail" in response_data:
            error_message = response_data["detail"]
        else:
            error_message = get_error_message(status_code, url, "POST")

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
        # get the message if available
        response_data = response.json()
        if isinstance(response_data, dict) and "detail" in response_data:
            error_message = response_data["detail"]  # pragma: no cover
        else:
            error_message = get_error_message(status_code, url, "DELETE")

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
