#!/bin/bash
set -e

# Docker entrypoint script for Basic Memory
# This script helps configure Basic Memory in a containerized environment

echo "Starting Basic Memory Docker container..."

# Create configuration directory if it doesn't exist
mkdir -p /root/.basic-memory

# If no config exists, create a default one with proper project mapping
CONFIG_FILE="/root/.basic-memory/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Creating default configuration..."
    cat > "$CONFIG_FILE" << EOF
{
  "env": "user",
  "projects": {
    "main": "/data/knowledge"
  },
  "default_project": "main",
  "log_level": "INFO",
  "sync_delay": 1000,
  "update_permalinks_on_move": false,
  "sync_changes": true
}
EOF
fi

# Ensure project directories exist
if [ -d "/data/knowledge" ]; then
    echo "Knowledge directory found at /data/knowledge"
else
    echo "Warning: No knowledge directory mounted at /data/knowledge"
    echo "Consider mounting your Obsidian vault with: -v /path/to/your/vault:/data/knowledge"
fi

# Handle additional project directories
for dir in /data/projects/*/; do
    if [ -d "$dir" ]; then
        project_name=$(basename "$dir")
        echo "Found additional project directory: $project_name at $dir"
    fi
done

# Show configuration info
echo "Basic Memory Configuration:"
echo "- Config file: $CONFIG_FILE"
echo "- Main project directory: /data/knowledge"
echo "- Database: /root/.basic-memory/memory.db"
echo "- Command: $@"

# Execute the provided command
exec "$@"