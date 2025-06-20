FROM python:3.12-slim

WORKDIR /app

# Copy the project files
COPY . .

# Install pip and build dependencies
RUN pip install --upgrade pip \
&& pip install . --no-cache-dir --ignore-installed

# Use the basic-memory entrypoint to run the MCP server with default SSE transport
CMD ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]