# Docker Setup Guide

Basic Memory can be run in Docker containers to provide a consistent, isolated environment for your knowledge management system. This is particularly useful for integrating with existing Dockerized MCP servers or for deployment scenarios.

## Quick Start

### Option 1: Using Docker Compose (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/basicmachines-co/basic-memory.git
   cd basic-memory
   ```

2. **Update the docker-compose.yml:**
   Edit the volume mount to point to your Obsidian vault:
   ```yaml
   volumes:
     # Change './obsidian-vault' to your actual directory path
     - /path/to/your/obsidian-vault:/data/knowledge:rw
   ```

3. **Start the container:**
   ```bash
   docker-compose up -d
   ```

### Option 2: Using Docker CLI

```bash
# Build the image
docker build -t basic-memory .

# Run with volume mounting
docker run -d \
  --name basic-memory-server \
  -v /path/to/your/obsidian-vault:/data/knowledge:rw \
  -v basic-memory-config:/root/.basic-memory:rw \
  -e BASIC_MEMORY_PROJECTS='{"main": "/data/knowledge"}' \
  -e BASIC_MEMORY_DEFAULT_PROJECT=main \
  basic-memory
```

## Configuration

### Volume Mounts

Basic Memory requires several volume mounts for proper operation:

1. **Knowledge Directory** (Required):
   ```yaml
   - /path/to/your/obsidian-vault:/data/knowledge:rw
   ```
   Mount your Obsidian vault or knowledge base directory.

2. **Configuration and Database** (Recommended):
   ```yaml
   - basic-memory-config:/root/.basic-memory:rw
   ```
   Persistent storage for configuration and SQLite database.

3. **Multiple Projects** (Optional):
   ```yaml
   - /path/to/project1:/data/projects/project1:rw
   - /path/to/project2:/data/projects/project2:rw
   ```

### Environment Variables

Configure Basic Memory using environment variables:

```yaml
environment:
  # Project configuration (JSON format)
  - BASIC_MEMORY_PROJECTS={"main": "/data/knowledge", "work": "/data/projects/work"}
  
  # Default project
  - BASIC_MEMORY_DEFAULT_PROJECT=main
  
  # Enable real-time sync
  - BASIC_MEMORY_SYNC_CHANGES=true
  
  # Logging level
  - BASIC_MEMORY_LOG_LEVEL=INFO
  
  # Sync delay in milliseconds
  - BASIC_MEMORY_SYNC_DELAY=1000
```

## MCP Server Transport Options

### STDIO Transport (Default)

Best for MCP client integration:

```yaml
command: ["basic-memory", "mcp"]
```

No port exposure needed. Communicates via standard input/output.

### HTTP Transport

For HTTP-based integrations:

```yaml
command: ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
ports:
  - "8000:8000"
```

## Use Cases

### 1. Obsidian Vault Integration

Mount your existing Obsidian vault:

```yaml
version: '3.8'
services:
  basic-memory:
    build: .
    volumes:
      - /Users/yourname/Documents/ObsidianVault:/data/knowledge:rw
      - basic-memory-config:/root/.basic-memory:rw
    environment:
      - BASIC_MEMORY_PROJECTS={"obsidian": "/data/knowledge"}
      - BASIC_MEMORY_DEFAULT_PROJECT=obsidian
```

### 2. Multiple Knowledge Bases

Support multiple projects:

```yaml
version: '3.8'
services:
  basic-memory:
    build: .
    volumes:
      - /path/to/personal-notes:/data/projects/personal:rw
      - /path/to/work-notes:/data/projects/work:rw
      - basic-memory-config:/root/.basic-memory:rw
    environment:
      - BASIC_MEMORY_PROJECTS={"personal": "/data/projects/personal", "work": "/data/projects/work"}
      - BASIC_MEMORY_DEFAULT_PROJECT=personal
```

### 3. MCP Server Integration

For integration with existing MCP server setups:

```yaml
version: '3.8'
services:
  mcp-server:
    image: your-mcp-server
    depends_on:
      - basic-memory
    # ... your MCP server configuration
    
  basic-memory:
    build: .
    volumes:
      - ./knowledge:/data/knowledge:rw
    command: ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "8000:8000"
```

## File Permissions

### Linux/macOS

Ensure your knowledge directories have proper permissions:

```bash
# Make directories readable/writable
chmod -R 755 /path/to/your/obsidian-vault

# If using specific user/group
chown -R $USER:$USER /path/to/your/obsidian-vault
```

### Windows

When using Docker Desktop on Windows, ensure the directories are shared:

1. Open Docker Desktop
2. Go to Settings → Resources → File Sharing
3. Add your knowledge directory path
4. Apply & Restart

## Troubleshooting

### Common Issues

1. **File Watching Not Working:**
   - Ensure volume mounts are read-write (`:rw`)
   - Check directory permissions
   - On Linux, may need to increase inotify limits:
     ```bash
     echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
     sudo sysctl -p
     ```

2. **Configuration Not Persisting:**
   - Use named volumes for `/root/.basic-memory`
   - Check volume mount permissions

3. **Network Connectivity:**
   - For HTTP transport, ensure port 8000 is exposed
   - Check firewall settings

### Debug Mode

Run with debug logging:

```yaml
environment:
  - BASIC_MEMORY_LOG_LEVEL=DEBUG
```

View logs:
```bash
docker-compose logs -f basic-memory
```

## Advanced Configuration

### Custom Entry Point

Use the provided entrypoint script for better configuration management:

```dockerfile
# In your custom Dockerfile
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["basic-memory", "mcp"]
```

### Health Checks

Monitor container health:

```yaml
healthcheck:
  test: ["CMD", "basic-memory", "--version"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

### Resource Limits

Set appropriate resource limits:

```yaml
deploy:
  resources:
    limits:
      memory: 512M
      cpus: '0.5'
    reservations:
      memory: 256M
      cpus: '0.25'
```

## Security Considerations

1. **Use Non-Root User:**
   The default Dockerfile runs as root. Consider creating a custom Dockerfile with a non-root user for production.

2. **Volume Permissions:**
   Ensure mounted directories have appropriate permissions and don't expose sensitive data.

3. **Network Security:**
   If using HTTP transport, consider using reverse proxy with SSL/TLS.

4. **Secrets Management:**
   Use Docker secrets or environment files for sensitive configuration.

## Integration Examples

### Claude Desktop with Docker

Configure Claude Desktop to use the containerized Basic Memory:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "docker",
      "args": [
        "exec",
        "basic-memory-server",
        "basic-memory",
        "mcp"
      ]
    }
  }
}
```

### VS Code with Docker

For VS Code MCP integration:

```json
{
  "mcp": {
    "servers": {
      "basic-memory": {
        "command": "docker",
        "args": ["exec", "basic-memory-server", "basic-memory", "mcp"]
      }
    }
  }
}
```

## Building Custom Images

### Using uv for Faster Builds

```dockerfile
FROM python:3.12-slim

# Install uv for faster dependency resolution
RUN pip install uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system .

COPY . .
CMD ["basic-memory", "mcp"]
```

### Multi-Stage Build

```dockerfile
# Build stage
FROM python:3.12-slim as builder
RUN pip install uv
WORKDIR /app
COPY . .
RUN uv pip install --system .

# Runtime stage
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/basic-memory /usr/local/bin/
CMD ["basic-memory", "mcp"]
```

## Support

For Docker-specific issues:

1. Check the [troubleshooting section](#troubleshooting) above
2. Review container logs: `docker-compose logs basic-memory`
3. Verify volume mounts: `docker inspect basic-memory-server`
4. Test file permissions: `docker exec basic-memory-server ls -la /data/knowledge`

For general Basic Memory support, see the main [README](../README.md) and [documentation](https://memory.basicmachines.co/).