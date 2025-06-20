FROM python:3.12-slim-bookworm

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1


# Create non-root user for security
RUN groupadd --gid 1000 basicmemory \
    && useradd --uid 1000 --gid basicmemory --shell /bin/bash --create-home basicmemory

# Copy the project into the image
ADD . /app

# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app
RUN uv sync --locked

# Create data directory and set ownership
RUN mkdir -p /app/data /home/basicmemory/.basic-memory \
    && chown -R basicmemory:basicmemory /app /home/basicmemory

# Switch to non-root user
USER basicmemory

# Set default data directory and add venv to PATH
ENV BASIC_MEMORY_HOME=/app/data \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /home/basicmemory

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD basic-memory --version || exit 1

# Use the basic-memory entrypoint to run the MCP server with default SSE transport
CMD ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]