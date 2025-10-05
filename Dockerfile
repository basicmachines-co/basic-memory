FROM python:3.12-slim-bookworm

# Build arguments for user ID and group ID (defaults to 1000)
ARG UID=1000
ARG GID=1000

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create a group and user with the provided UID/GID
# Check if the GID already exists, if not create appgroup
RUN (getent group ${GID} || groupadd --gid ${GID} appgroup) && \
    useradd --uid ${UID} --gid ${GID} --create-home --shell /bin/bash appuser

# Copy the project into the image
ADD . /app

# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app
RUN uv sync --locked

# Create necessary directories and set ownership
RUN mkdir -p /app/data/basic-memory /app/.basic-memory && \
    chown -R appuser:${GID} /app

# Set default data directory and add venv to PATH
ENV BASIC_MEMORY_HOME=/app/data/basic-memory \
    BASIC_MEMORY_PROJECT_ROOT=/app/data \
    PATH="/app/.venv/bin:$PATH"

# Switch to the non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD basic-memory --version || exit 1

# Use the basic-memory entrypoint to run the MCP server with default SSE transport
CMD ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]