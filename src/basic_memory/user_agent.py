"""The User-Agent Basic Memory sends to Basic Memory Cloud.

httpx's default (`python-httpx/x`) looks the same as any script, so Cloud could not
tell our own CLI and MCP server from unknown clients when applying fair-use and rate
limits. Every HTTP client that calls Cloud sets this as a client default; a header a
caller passes on one request still replaces it, as httpx merges request headers over
client headers.

The MCP server and the CLI share one process model and no surface flag, so both send
`(cli)`.
"""

from basic_memory import __version__

BASIC_MEMORY_USER_AGENT = f"basic-memory/{__version__} (cli)"
