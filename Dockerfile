FROM python:3.12-slim-bookworm

# Build arguments for user ID and group ID (defaults to 1000)
ARG UID=1000
ARG GID=1000

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
# UV_PYTHON_INSTALL_DIR ensures Python is installed to a persistent location
# that survives in the final image (not in /root/.local which gets lost)
# UV_PYTHON_PREFERENCE=only-managed tells uv to use its managed Python version
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed

# Create a group and user with the provided UID/GID
# Check if the GID already exists, if not create appgroup
RUN (getent group ${GID} || groupadd --gid ${GID} appgroup) && \
    useradd --uid ${UID} --gid ${GID} --create-home --shell /bin/bash appuser

# Copy the project into the image
ADD . /app

# Install Python 3.13 explicitly and sync the project
WORKDIR /app
RUN uv python install 3.13
RUN uv sync --locked --python 3.13

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
