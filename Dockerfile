# Multi-stage build using uv for optimal performance and smaller images

# Build stage with dependencies
FROM python:3.12-slim AS builder

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system dependencies needed for building
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specification files first for better caching
COPY pyproject.toml uv.lock* ./

# Install dependencies into virtual environment with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy source code and install project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Production stage with minimal image
FROM python:3.12-slim

# Set environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Create non-root user for security
RUN groupadd --gid 1000 basicmemory \
    && useradd --uid 1000 --gid basicmemory --shell /bin/bash --create-home basicmemory

# Install runtime dependencies only
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder --chown=basicmemory:basicmemory /app/.venv /app/.venv

# Create data directory and set ownership
RUN mkdir -p /app/data /home/basicmemory/.basic-memory \
    && chown -R basicmemory:basicmemory /app /home/basicmemory

# Switch to non-root user
USER basicmemory

# Set default data directory
ENV BASIC_MEMORY_HOME=/app/data

WORKDIR /home/basicmemory

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Use the basic-memory entrypoint to run the MCP server with default SSE transport
CMD ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]