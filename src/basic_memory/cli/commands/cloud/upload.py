"""WebDAV upload functionality for basic-memory projects."""

import os
from pathlib import Path
from contextlib import AbstractAsyncContextManager
from typing import Callable

import aiofiles
import httpx

from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.tools.utils import call_put

# Archive file extensions that should be skipped during upload
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz", ".tbz2"}


async def upload_path(
    local_path: Path,
    project_name: str,
    verbose: bool = False,
    use_gitignore: bool = True,
    dry_run: bool = False,
    *,
    client_cm_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]] | None = None,
    put_func=call_put,
) -> bool:
    """
    Upload a file or directory to cloud project via WebDAV.

    Args:
        local_path: Path to local file or directory
        project_name: Name of cloud project (destination)
        verbose: Show detailed information about filtering and upload
        use_gitignore: If False, skip .gitignore patterns (still use .bmignore)
        dry_run: If True, show what would be uploaded without uploading

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
            if verbose:
                print(f"Uploading single file: {local_path.name}")
        else:
            files_to_upload = _get_files_to_upload(local_path, verbose, use_gitignore)

        if not files_to_upload:
            print("No files found to upload")
            if verbose:
                print(
                    "\nTip: Use --verbose to see which files are being filtered, "
                    "or --no-gitignore to skip .gitignore patterns"
                )
            return True

        print(f"Found {len(files_to_upload)} file(s) to upload")

        # Calculate total size
        total_bytes = sum(file_path.stat().st_size for file_path, _ in files_to_upload)
        skipped_count = 0

        # If dry run, just show what would be uploaded
        if dry_run:
            print("\nFiles that would be uploaded:")
            for file_path, relative_path in files_to_upload:
                # Skip archive files
                if _is_archive_file(file_path):
                    print(f"  [SKIP] {relative_path} (archive file)")
                    skipped_count += 1
                    continue

                size = file_path.stat().st_size
                if size < 1024:
                    size_str = f"{size} bytes"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"
                print(f"  {relative_path} ({size_str})")
        else:
            # Upload files using httpx.
            # Allow injection for tests (MockTransport) while keeping production default.
            cm_factory = client_cm_factory or get_client
            async with cm_factory() as client:
                for i, (file_path, relative_path) in enumerate(files_to_upload, 1):
                    # Skip archive files (zip, tar, gz, etc.)
                    if _is_archive_file(file_path):
                        print(
                            f"Skipping archive file: {relative_path} ({i}/{len(files_to_upload)})"
                        )
                        skipped_count += 1
                        continue

                    # Build remote path: /webdav/{project_name}/{relative_path}
                    remote_path = f"/webdav/{project_name}/{relative_path}"
                    print(f"Uploading {relative_path} ({i}/{len(files_to_upload)})")

                    # Get file modification time
                    file_stat = file_path.stat()
                    mtime = int(file_stat.st_mtime)

                    # Read file content asynchronously
                    async with aiofiles.open(file_path, "rb") as f:
                        content = await f.read()

                    # Upload via HTTP PUT to WebDAV endpoint with mtime header
                    # Using X-OC-Mtime (ownCloud/Nextcloud standard)
                    response = await put_func(
                        client, remote_path, content=content, headers={"X-OC-Mtime": str(mtime)}
                    )
                    response.raise_for_status()

        # Format total size based on magnitude
        if total_bytes < 1024:
            size_str = f"{total_bytes} bytes"
        elif total_bytes < 1024 * 1024:
            size_str = f"{total_bytes / 1024:.1f} KB"
        else:
            size_str = f"{total_bytes / (1024 * 1024):.1f} MB"

        uploaded_count = len(files_to_upload) - skipped_count
        if dry_run:
            print(f"\nTotal: {uploaded_count} file(s) ({size_str})")
            if skipped_count > 0:
                print(f"  Would skip {skipped_count} archive file(s)")
        else:
            print(f"✓ Upload complete: {uploaded_count} file(s) ({size_str})")
            if skipped_count > 0:
                print(f"  Skipped {skipped_count} archive file(s)")

        return True

    except httpx.HTTPStatusError as e:
        print(f"Upload failed: HTTP {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        print(f"Upload failed: {e}")
        return False


def _is_archive_file(file_path: Path) -> bool:
    """
    Check if a file is an archive file based on its extension.

    Args:
        file_path: Path to the file to check

    Returns:
        True if file is an archive, False otherwise
    """
    return file_path.suffix.lower() in ARCHIVE_EXTENSIONS


def _get_files_to_upload(
    directory: Path, verbose: bool = False, use_gitignore: bool = True
) -> list[tuple[Path, str]]:
    """
    Get list of files to upload from directory.

    Uses .bmignore and optionally .gitignore patterns for filtering.

    Args:
        directory: Directory to scan
        verbose: Show detailed filtering information
        use_gitignore: If False, skip .gitignore patterns (still use .bmignore)

    Returns:
        List of (absolute_path, relative_path) tuples
    """
    files = []
    ignored_files = []

    # Load ignore patterns from .bmignore and optionally .gitignore
    ignore_patterns = load_gitignore_patterns(directory, use_gitignore=use_gitignore)

    if verbose:
        gitignore_path = directory / ".gitignore"
        gitignore_exists = gitignore_path.exists() and use_gitignore
        print(f"\nScanning directory: {directory}")
        print("Using .bmignore: Yes")
        print(f"Using .gitignore: {'Yes' if gitignore_exists else 'No'}")
        print(f"Ignore patterns loaded: {len(ignore_patterns)}")
        if ignore_patterns and len(ignore_patterns) <= 20:
            print(f"Patterns: {', '.join(sorted(ignore_patterns))}")
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
                    print(f"  [IGNORED DIR] {rel_path}/")
            else:
                filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        # Process files
        for filename in filenames:
            file_path = root_path / filename

            # Calculate relative path for display/remote
            rel_path = file_path.relative_to(directory)
            remote_path = str(rel_path).replace("\\", "/")

            # Check if file should be ignored
            if should_ignore_path(file_path, directory, ignore_patterns):
                ignored_files.append(remote_path)
                if verbose:
                    print(f"  [IGNORED] {remote_path}")
                continue

            if verbose:
                print(f"  [INCLUDE] {remote_path}")

            files.append((file_path, remote_path))

    if verbose:
        print("\nSummary:")
        print(f"  Files to upload: {len(files)}")
        print(f"  Files ignored: {len(ignored_files)}")

    return files
