"""Utility functions for making HTTP requests in Basic Memory MCP tools.

These functions provide a consistent interface for making HTTP requests
to the Basic Memory API, with improved error handling and logging.
"""

import asyncio
import random
import typing
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import logfire
from httpx import (
    Response,
    URL,
    AsyncClient,
    HTTPStatusError,
    Headers,
    TimeoutException,
    TransportError,
)
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
from fastmcp.exceptions import ToolError

from basic_memory.config import ConfigManager

_RATE_LIMIT_MAX_ATTEMPTS = 3
_RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS = 30.0
_RATE_LIMIT_JITTER_MIN_SECONDS = 0.05
_RATE_LIMIT_JITTER_MAX_SECONDS = 0.25

type _RequestCall = Callable[[], Awaitable[Response]]


def _classify_http_outcome(status_code: int) -> str:
    """Map HTTP status codes to a low-cardinality outcome label."""
    if 200 <= status_code < 300:
        return "success"
    if 300 <= status_code < 400:  # pragma: no cover
        return "redirect"
    if 400 <= status_code < 500:
        return "client_error"
    if 500 <= status_code < 600:
        return "server_error"
    return "unknown"  # pragma: no cover


def _response_span_attrs(response: Response) -> dict[str, Any]:
    """Attributes to attach to a request span after a response lands."""
    return {
        "status_code": response.status_code,
        "is_success": response.is_success,
        "outcome": _classify_http_outcome(response.status_code),
    }


def _transport_error_span_attrs(exc: Exception) -> dict[str, Any]:
    """Attributes to attach when the transport layer fails before any response."""
    return {
        "is_success": False,
        "outcome": "transport_error",
        "error_type": type(exc).__name__,
    }


def _request_headers(headers: HeaderTypes | None) -> HeaderTypes | None:
    """Merge request-local workspace permalink headers into outbound API calls."""
    from basic_memory.workspace_context import workspace_permalink_headers

    workspace_headers = workspace_permalink_headers()
    if not workspace_headers:
        return headers

    if headers is None:
        return workspace_headers

    merged_headers = Headers(headers)
    merged_headers.update(workspace_headers)
    return merged_headers


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


def _transport_error_message(exc: TransportError, url: URL | str, method: str) -> str:
    """Build an actionable message for transport failures that produced no response.

    httpx transport errors often stringify to an empty string (e.g. ReadTimeout),
    which is how blank errors like "Error removing project:" reached users (#1034),
    so the message always names the exception type explicitly.
    """
    # Extract path from URL for cleaner error messages
    if isinstance(url, str):
        path = url.split("/")[-1]
    else:
        path = str(url).split("/")[-1] if url else "resource"

    detail = str(exc).strip()
    described = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    if isinstance(exc, TimeoutException):
        return (
            f"Request timed out ({described}): the server did not respond to "
            f"{method} '{path}' in time. Long-running operations (such as deleting a "
            f"large project) may still be completing server-side — check the result "
            f"(e.g. `bm project list` for project deletes) before retrying."
        )
    return (
        f"Connection failed ({described}): the {method} request to '{path}' did not "
        f"complete. Check that the server is reachable, then retry."
    )


def _extract_response_data(response: Response) -> Any:
    """Decode the JSON payload of an API response for error reporting.

    Upstream gateways (Fly, Cloudflare, load balancers) can return HTML
    error pages before the request reaches our FastAPI app; those have no
    structured `detail` to surface, so we skip them. A malformed body with
    a JSON content-type is a server bug and we let it raise.
    """
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    return response.json()


def _response_detail_text(response_data: Any) -> str | None:
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


def _utc_now() -> datetime:
    """Return the current UTC time for HTTP-date Retry-After values."""
    return datetime.now(timezone.utc)


def _parse_retry_after(response: Response) -> tuple[str, float] | None:
    """Parse a Retry-After delay-seconds or HTTP-date header."""
    raw_retry_after = response.headers.get("Retry-After")
    if raw_retry_after is None:
        return None

    retry_after = raw_retry_after.strip()
    if retry_after.isascii() and retry_after.isdigit():
        normalized_delay = retry_after.lstrip("0") or "0"
        max_wait_text = str(int(_RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS))
        if len(normalized_delay) > len(max_wait_text) or (
            len(normalized_delay) == len(max_wait_text) and normalized_delay > max_wait_text
        ):
            return raw_retry_after, float("inf")
        return raw_retry_after, float(int(normalized_delay))

    try:
        retry_at = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError, OverflowError):
        return None

    if retry_at.tzinfo is None:
        return None

    delay_seconds = (retry_at.astimezone(timezone.utc) - _utc_now()).total_seconds()
    return raw_retry_after, max(0.0, delay_seconds)


def _request_body_is_replayable(
    content: RequestContent | None,
    files: RequestFiles | None,
) -> bool:
    """Return whether httpx can safely rebuild the request body for another attempt."""
    return files is None and (content is None or isinstance(content, (str, bytes)))


def _with_rate_limit_context(message: str, context: str) -> str:
    """Preserve the server detail while adding actionable retry context."""
    separator = "" if message.endswith((".", "!", "?")) else "."
    return f"{message}{separator} {context}"


async def _request_with_rate_limit_retry(
    request: _RequestCall,
    *,
    method: str,
    url: URL | str,
    replayable: bool,
) -> tuple[Response, str | None]:
    """Retry Basic Memory gateway 429 responses within an explicit wait budget.

    The gateway rejects rate-limited requests before application processing, so replayable
    writes are safe to retry. This is a cumulative sleep budget, not an end-to-end request
    deadline; each individual HTTP attempt retains the caller's configured timeout.
    """
    total_wait_seconds = 0.0

    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        response = await request()
        if response.status_code != 429:
            return response, None

        response_data = _extract_response_data(response)
        error_message = _resolve_error_message(429, url, method, response_data)
        retry_boundary = _parse_retry_after(response)

        if retry_boundary is None:
            return response, _with_rate_limit_context(
                error_message,
                "Automatic retry skipped because Retry-After is missing or invalid.",
            )

        raw_retry_after, server_delay_seconds = retry_boundary
        boundary_text = f"Server Retry-After: {raw_retry_after}."

        if not replayable:
            return response, _with_rate_limit_context(
                error_message,
                "Automatic retry skipped because the request body cannot be safely replayed. "
                f"{boundary_text}",
            )

        if attempt == _RATE_LIMIT_MAX_ATTEMPTS:
            return response, _with_rate_limit_context(
                error_message,
                f"Rate-limit retry exhausted after {attempt} attempts. {boundary_text}",
            )

        jitter_seconds = random.uniform(
            _RATE_LIMIT_JITTER_MIN_SECONDS,
            _RATE_LIMIT_JITTER_MAX_SECONDS,
        )
        wait_seconds = server_delay_seconds + jitter_seconds
        if total_wait_seconds + wait_seconds > _RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS:
            return response, _with_rate_limit_context(
                error_message,
                "Rate-limit retry wait budget exhausted before the next attempt "
                f"(maximum cumulative wait {_RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS:g}s). "
                f"{boundary_text}",
            )

        logger.warning(
            f"Rate limit exceeded: {method} {url}; retrying in {wait_seconds:.3f}s "
            f"(attempt {attempt + 1}/{_RATE_LIMIT_MAX_ATTEMPTS})"
        )
        await response.aclose()
        await asyncio.sleep(wait_seconds)
        total_wait_seconds += wait_seconds

    raise AssertionError("rate-limit retry loop exhausted without returning")  # pragma: no cover


async def call_get(
    client: AsyncClient,
    url: URL | str,
    *,
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="GET",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=False,
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.get(
                    url,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="GET",
                url=url,
                replayable=True,
            )
            request_span.set_attributes(_response_span_attrs(response))

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "GET", response_data
        )

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
    except TransportError as e:
        # No HTTP response arrived (timeout, connect failure) — wrap with actionable text (#1034)
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "GET")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise


async def call_put(
    client: AsyncClient,
    url: URL | str,
    *,
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="PUT",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=any(value is not None for value in (content, data, files, json)),
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.put(
                    url,
                    content=content,
                    data=data,
                    files=files,
                    json=json,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="PUT",
                url=url,
                replayable=_request_body_is_replayable(content, files),
            )
            request_span.set_attributes(_response_span_attrs(response))

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code

        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "PUT", response_data
        )

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
    except TransportError as e:
        # No HTTP response arrived (timeout, connect failure) — wrap with actionable text (#1034)
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "PUT")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise


async def call_patch(
    client: AsyncClient,
    url: URL | str,
    *,
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    error_message = None
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="PATCH",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=any(value is not None for value in (content, data, files, json)),
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.patch(
                    url,
                    content=content,
                    data=data,
                    files=files,
                    json=json,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="PATCH",
                url=url,
                replayable=_request_body_is_replayable(content, files),
            )
            request_span.set_attributes(_response_span_attrs(response))

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code

        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "PATCH", response_data
        )

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
        if error_message is None:
            status_code = e.response.status_code
            response_data = _extract_response_data(e.response)
            error_message = _resolve_error_message(status_code, url, "PATCH", response_data)

        raise ToolError(error_message) from e
    except TransportError as e:
        # No HTTP response arrived (timeout, connect failure) — wrap with actionable text (#1034)
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "PATCH")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise


async def call_post(
    client: AsyncClient,
    url: URL | str,
    *,
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="POST",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=any(value is not None for value in (content, data, files, json)),
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.post(
                    url=url,
                    content=content,
                    data=data,
                    files=files,
                    json=json,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="POST",
                url=url,
                replayable=_request_body_is_replayable(content, files),
            )
            request_span.set_attributes(_response_span_attrs(response))
        logger.debug(f"response: {_extract_response_data(response)}")

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "POST", response_data
        )

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
    except TransportError as e:
        # No HTTP response arrived (timeout, connect failure) — wrap with actionable text (#1034)
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "POST")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise


async def call_query(
    client: AsyncClient,
    url: URL | str,
    *,
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    """Make a safe, idempotent QUERY request and handle errors appropriately."""
    logger.debug(f"Calling QUERY '{url}'")
    error_message = None
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="QUERY",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=any(value is not None for value in (content, data, files, json)),
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.request(
                    "QUERY",
                    url=url,
                    content=content,
                    data=data,
                    files=files,
                    json=json,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="QUERY",
                url=url,
                replayable=_request_body_is_replayable(content, files),
            )
            request_span.set_attributes(_response_span_attrs(response))

        if response.is_success:
            return response

        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "QUERY", response_data
        )

        if 400 <= status_code < 500:
            if status_code == 429:  # pragma: no cover
                logger.warning(f"Rate limit exceeded: QUERY {url}: {error_message}")
            else:
                logger.info(f"Client error: QUERY {url}: {error_message}")
        else:
            logger.error(f"Server error: QUERY {url}: {error_message}")

        response.raise_for_status()
        return response  # pragma: no cover

    except HTTPStatusError as e:
        raise ToolError(error_message) from e
    except TransportError as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "QUERY")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise


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
    client_name: str | None = None,
    operation: str | None = None,
    path_template: str | None = None,
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
    request_span: logfire.LogfireSpan | None = None

    try:
        with logfire.span(
            "mcp.http.request",
            method="DELETE",
            client_name=client_name,
            operation=operation,
            path_template=path_template,
            phase="request",
            has_query=bool(params),
            has_body=False,
        ) as request_span:
            response, rate_limit_error_message = await _request_with_rate_limit_retry(
                lambda: client.delete(
                    url=url,
                    params=params,
                    headers=_request_headers(headers),
                    cookies=cookies,
                    auth=auth,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    extensions=extensions,
                ),
                method="DELETE",
                url=url,
                replayable=True,
            )
            request_span.set_attributes(_response_span_attrs(response))

        if response.is_success:
            return response

        # Handle different status codes differently
        status_code = response.status_code
        response_data = _extract_response_data(response)
        error_message = rate_limit_error_message or _resolve_error_message(
            status_code, url, "DELETE", response_data
        )

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
    except TransportError as e:
        # No HTTP response arrived (timeout, connect failure) — wrap with actionable text (#1034)
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise ToolError(_transport_error_message(e, url, "DELETE")) from e
    except Exception as e:
        if request_span is not None:
            request_span.set_attributes(_transport_error_span_attrs(e))
        raise
