"""Tool call history tracking for MCP operations."""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, TypeVar
from functools import wraps

# Type variable for generic function type
F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolCall:
    """Represents a single tool call in history."""

    id: str
    timestamp: float
    tool_name: str
    status: str  # "success", "error", "running"
    execution_time_ms: Optional[float] = None
    input: Optional[Dict[str, Any]] = None
    output: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        # Convert timestamp to ISO format
        result["timestamp"] = datetime.fromtimestamp(self.timestamp).isoformat() + "Z"
        return result


class ToolHistoryTracker:
    """Singleton tracker for tool call history."""

    _instance: Optional["ToolHistoryTracker"] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> "ToolHistoryTracker":
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_history: int = 1000):
        """Initialize the tracker with a maximum history size."""
        if self._initialized:
            return

        self.max_history = max_history
        self.history: Deque[ToolCall] = deque(maxlen=max_history)
        self._call_counter = 0
        self._lock = asyncio.Lock()
        self._initialized = True

    async def add_call(
        self,
        tool_name: str,
        input_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a new tool call to history and return its ID."""
        async with self._lock:
            self._call_counter += 1
            call_id = f"call_{self._call_counter:06d}_{int(time.time() * 1000)}"

            tool_call = ToolCall(
                id=call_id,
                timestamp=time.time(),
                tool_name=tool_name,
                status="running",
                input=input_params,
            )

            self.history.append(tool_call)
            return call_id

    async def update_call(
        self,
        call_id: str,
        status: str,
        execution_time_ms: Optional[float] = None,
        output: Optional[Any] = None,
        error: Optional[str] = None,
    ):
        """Update an existing tool call with results."""
        async with self._lock:
            for call in self.history:
                if call.id == call_id:
                    call.status = status
                    call.execution_time_ms = execution_time_ms
                    call.output = output
                    call.error = error
                    break

    async def get_history(
        self,
        limit: int = 10,
        tool_name: Optional[str] = None,
        include_inputs: bool = True,
        include_outputs: bool = False,
        since: Optional[str] = None,
    ) -> List[ToolCall]:
        """Get filtered tool call history."""
        async with self._lock:
            # Convert to list for filtering
            calls = list(self.history)

            # Filter by time if specified
            if since:
                cutoff_time = self._parse_time_filter(since)
                calls = [c for c in calls if c.timestamp >= cutoff_time]

            # Filter by tool name if specified
            if tool_name:
                calls = [c for c in calls if c.tool_name == tool_name]

            # Sort by timestamp (most recent first)
            calls.sort(key=lambda x: x.timestamp, reverse=True)

            # Limit results
            calls = calls[:limit]

            # Process outputs based on flags
            result = []
            for call in calls:
                call_copy = ToolCall(
                    id=call.id,
                    timestamp=call.timestamp,
                    tool_name=call.tool_name,
                    status=call.status,
                    execution_time_ms=call.execution_time_ms,
                    input=call.input if include_inputs else None,
                    output=call.output if include_outputs else None,
                    error=call.error,
                )
                result.append(call_copy)

            return result

    def _parse_time_filter(self, since: str) -> float:
        """Parse time filter string and return timestamp."""
        now = time.time()

        # Handle relative times like "1h ago", "2d ago"
        if "ago" in since.lower():
            parts = since.lower().replace("ago", "").strip().split()
            if len(parts) == 1:
                value_unit = parts[0]
                # Try to parse combined format like "1h"
                import re
                match = re.match(r"(\d+)([hdmw])", value_unit)
                if match:
                    value = int(match.group(1))
                    unit = match.group(2)
                else:
                    return now
            elif len(parts) == 2:
                value = int(parts[0])
                unit = parts[1][0]  # First letter of unit
            else:
                return now

            if unit == "h":  # hours
                return now - (value * 3600)
            elif unit == "d":  # days
                return now - (value * 86400)
            elif unit == "m":  # minutes
                return now - (value * 60)
            elif unit == "w":  # weeks
                return now - (value * 604800)

        # Handle absolute dates like "2024-01-20"
        try:
            dt = datetime.fromisoformat(since)
            return dt.timestamp()
        except:
            pass

        # Default to current time if parsing fails
        return now

    async def clear_history(self):
        """Clear all tool call history."""
        async with self._lock:
            self.history.clear()
            self._call_counter = 0

    async def get_call_by_id(self, call_id: str) -> Optional[ToolCall]:
        """Get a specific tool call by ID."""
        async with self._lock:
            for call in self.history:
                if call.id == call_id:
                    return call
            return None


def track_tool_call(func: F) -> F:
    """Decorator to track MCP tool calls."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        """Wrapper that tracks tool execution."""
        tracker = ToolHistoryTracker()

        # Extract tool name from function
        tool_name = func.__name__

        # Filter out Context parameter if present
        filtered_kwargs = {k: v for k, v in kwargs.items() if k != "context"}

        # Record the call
        call_id = await tracker.add_call(tool_name, filtered_kwargs)

        # Execute the tool
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            execution_time = (time.time() - start_time) * 1000

            # Update with success
            await tracker.update_call(
                call_id,
                status="success",
                execution_time_ms=execution_time,
                output=result if isinstance(result, (str, int, float, bool)) else str(result)[:500],
            )

            return result

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000

            # Update with error
            await tracker.update_call(
                call_id,
                status="error",
                execution_time_ms=execution_time,
                error=str(e),
            )

            raise

    return wrapper  # type: ignore


# Singleton instance getter for convenience
def get_tracker() -> ToolHistoryTracker:
    """Get the singleton ToolHistoryTracker instance."""
    return ToolHistoryTracker()