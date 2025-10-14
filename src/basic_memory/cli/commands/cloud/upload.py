"""WebDAV upload functionality for basic-memory projects."""

import os
from pathlib import Path

import aiofiles
import httpx

from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.tools.utils import call_put


async def upload_path(local_path: Path, project_name: str) -> bool:
    """
    Upload a file or directory to cloud project via WebDAV.

    Args:
        local_path: Path to local file or directory
        project_name: Name of cloud project (destination)

    Returns:
        True if upload succeeded, False otherwise
    """
    try:
        # Resolve path
        local_path = local_path.resolve()

        # Check if path exists
        if not local_path.exists():
            print(f"Error: Path does not exist: {local_path}")
            return False

        # Get files to upload
        if local_path.is_file():
            files_to_upload = [(local_path, local_path.name)]
        else:
            files_to_upload = _get_files_to_upload(local_path)

        if not files_to_upload:
            print("No files found to upload")
            return True

        print(f"Found {len(files_to_upload)} file(s) to upload")

        # Upload files using httpx
        total_bytes = 0

        async with get_client() as client:
            for i, (file_path, relative_path) in enumerate(files_to_upload, 1):
                # Build remote path: /webdav/{project_name}/{relative_path}
                remote_path = f"/webdav/{project_name}/{relative_path}"
                print(f"Uploading {relative_path} ({i}/{len(files_to_upload)})")

                # Read file content asynchronously
                async with aiofiles.open(file_path, "rb") as f:
                    content = await f.read()

                # Upload via HTTP PUT to WebDAV endpoint
                response = await call_put(client, remote_path, content=content)
                response.raise_for_status()

                total_bytes += file_path.stat().st_size

        # Format size based on magnitude
        if total_bytes < 1024:
            size_str = f"{total_bytes} bytes"
        elif total_bytes < 1024 * 1024:
            size_str = f"{total_bytes / 1024:.1f} KB"
        else:
            size_str = f"{total_bytes / (1024 * 1024):.1f} MB"

        print(f"✓ Upload complete: {len(files_to_upload)} file(s) ({size_str})")
        return True

    except httpx.HTTPStatusError as e:
        print(f"Upload failed: HTTP {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        print(f"Upload failed: {e}")
        return False


def _get_files_to_upload(directory: Path) -> list[tuple[Path, str]]:
    """
    Get list of files to upload from directory.

    Uses .bmignore and .gitignore patterns for filtering.

    Args:
        directory: Directory to scan

    Returns:
        List of (absolute_path, relative_path) tuples
    """
    files = []

    # Load ignore patterns from .bmignore and .gitignore
    ignore_patterns = load_gitignore_patterns(directory)

    # Walk through directory
    for root, dirs, filenames in os.walk(directory):
        root_path = Path(root)

        # Filter directories based on ignore patterns
        filtered_dirs = []
        for d in dirs:
            dir_path = root_path / d
            if not should_ignore_path(dir_path, directory, ignore_patterns):
                filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        # Process files
        for filename in filenames:
            file_path = root_path / filename

            # Check if file should be ignored
            if should_ignore_path(file_path, directory, ignore_patterns):
                continue

            # Calculate relative path for remote
            rel_path = file_path.relative_to(directory)
            # Use forward slashes for WebDAV paths
            remote_path = str(rel_path).replace("\\", "/")

            files.append((file_path, remote_path))

    return files
