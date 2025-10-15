"""WebDAV upload functionality for basic-memory projects."""

import os
from pathlib import Path

import aiofiles
import httpx

from basic_memory.ignore_utils import (
    load_bmignore_only_patterns,
    load_gitignore_patterns,
    should_ignore_path,
)
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.tools.utils import call_put


async def upload_path(
    local_path: Path, project_name: str, verbose: bool = False, no_gitignore: bool = False
) -> bool:
    """
    Upload a file or directory to cloud project via WebDAV.

    Args:
        local_path: Path to local file or directory
        project_name: Name of cloud project (destination)
        verbose: Show detailed information about filtering and uploads
        no_gitignore: Skip loading patterns from .gitignore files

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
            files_to_upload = _get_files_to_upload(local_path, verbose, no_gitignore)

        if not files_to_upload:
            print("No files found to upload")
            return True

        if verbose:
            print(f"\nFiles to upload ({len(files_to_upload)}):")
            for file_path, rel_path in files_to_upload:
                print(f"  {rel_path}")
            print()
        else:
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


def _get_files_to_upload(
    directory: Path, verbose: bool = False, no_gitignore: bool = False
) -> list[tuple[Path, str]]:
    """
    Get list of files to upload from directory.

    Uses .bmignore and optionally .gitignore patterns for filtering.

    Args:
        directory: Directory to scan
        verbose: Show detailed information about filtering
        no_gitignore: Skip loading patterns from .gitignore files

    Returns:
        List of (absolute_path, relative_path) tuples
    """
    files = []
    ignored_files: list[tuple[str, str]] = []  # (relative_path, matching_pattern)
    total_files_found = 0

    # Load ignore patterns
    if no_gitignore:
        ignore_patterns = load_bmignore_only_patterns()
        if verbose:
            print(f"Loaded {len(ignore_patterns)} patterns from .bmignore only")
            print("  (--no-gitignore flag: skipping .gitignore patterns)")
    else:
        ignore_patterns = load_gitignore_patterns(directory)
        if verbose:
            bmignore_count = len(load_bmignore_only_patterns())
            gitignore_count = len(ignore_patterns) - bmignore_count
            print(f"Loaded ignore patterns:")
            print(f"  .bmignore: {bmignore_count} patterns")
            print(f"  .gitignore: {gitignore_count} patterns")
            print(f"  Total: {len(ignore_patterns)} patterns")

    if verbose and ignore_patterns:
        print(f"\nIgnore patterns being applied:")
        for pattern in sorted(ignore_patterns):
            print(f"  {pattern}")
        print()

    # Walk through directory
    for root, dirs, filenames in os.walk(directory):
        root_path = Path(root)

        # Filter directories based on ignore patterns
        filtered_dirs = []
        for d in dirs:
            dir_path = root_path / d
            if should_ignore_path(dir_path, directory, ignore_patterns):
                if verbose:
                    rel_path = dir_path.relative_to(directory)
                    # Find which pattern matched
                    matching_pattern = _find_matching_pattern(
                        dir_path, directory, ignore_patterns
                    )
                    ignored_files.append((f"{rel_path}/", matching_pattern))
            else:
                filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        # Process files
        for filename in filenames:
            file_path = root_path / filename
            total_files_found += 1

            # Check if file should be ignored
            if should_ignore_path(file_path, directory, ignore_patterns):
                if verbose:
                    rel_path = file_path.relative_to(directory)
                    # Find which pattern matched
                    matching_pattern = _find_matching_pattern(
                        file_path, directory, ignore_patterns
                    )
                    ignored_files.append((str(rel_path), matching_pattern))
                continue

            # Calculate relative path for remote
            rel_path = file_path.relative_to(directory)
            # Use forward slashes for WebDAV paths
            remote_path = str(rel_path).replace("\\", "/")

            files.append((file_path, remote_path))

    if verbose:
        print(f"Scan results:")
        print(f"  Total files found: {total_files_found}")
        print(f"  Files to upload: {len(files)}")
        print(f"  Files/dirs ignored: {len(ignored_files)}")

        if ignored_files:
            print(f"\nIgnored files and directories:")
            for rel_path, pattern in ignored_files:
                print(f"  {rel_path} (matched: {pattern})")
        print()

    return files


def _find_matching_pattern(file_path: Path, base_path: Path, ignore_patterns: set[str]) -> str:
    """Find which pattern caused a file to be ignored.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        The first matching pattern, or "unknown" if none found
    """
    import fnmatch

    try:
        relative_path = file_path.relative_to(base_path)
        relative_str = str(relative_path)
        relative_posix = relative_path.as_posix()

        for pattern in ignore_patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                root_pattern = pattern[1:]
                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]
                    if len(relative_path.parts) > 0 and relative_path.parts[0] == dir_name:
                        return pattern
                else:
                    if fnmatch.fnmatch(relative_posix, root_pattern):
                        return pattern
                continue

            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_name = pattern[:-1]
                if dir_name in relative_path.parts:
                    return pattern
                continue

            # Direct name match
            if pattern in relative_path.parts:
                return pattern

            # Check path parts
            for part in relative_path.parts:
                if fnmatch.fnmatch(part, pattern):
                    return pattern

            # Glob pattern match on full path
            if fnmatch.fnmatch(relative_posix, pattern) or fnmatch.fnmatch(relative_str, pattern):
                return pattern

        return "unknown"
    except ValueError:
        return "unknown"
