#!/bin/bash
# Ensure config and data directories exist and are writable
# Railway volumes mount as root, so we need to create subdirs
mkdir -p "${BASIC_MEMORY_CONFIG_DIR:-/app/data/.config}"
mkdir -p "${BASIC_MEMORY_HOME:-/app/data/shared}"

exec "$@"
