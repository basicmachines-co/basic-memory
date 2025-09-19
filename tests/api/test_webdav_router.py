"""Tests for WebDAV router endpoints."""

import json
import time
import pytest


@pytest.mark.asyncio
async def test_webdav_options_file(client, project_config, project_url):
    """Test WebDAV OPTIONS for a file."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = project_config.home / "test.md"
    test_file.write_text(content)

    # Test OPTIONS request
    response = await client.request("OPTIONS", f"{project_url}/webdav/test.md")
    assert response.status_code == 204
    assert "DAV" in response.headers
    assert response.headers["DAV"] == "1,2"
    assert "Allow" in response.headers
    assert "PUT" in response.headers["Allow"]
    assert "GET" in response.headers["Allow"]
    assert "DELETE" in response.headers["Allow"]


@pytest.mark.asyncio
async def test_webdav_options_directory(client, project_config, project_url):
    """Test WebDAV OPTIONS for a directory."""
    # Create a test directory
    test_dir = project_config.home / "test_dir"
    test_dir.mkdir(exist_ok=True)

    # Test OPTIONS request
    response = await client.request("OPTIONS", f"{project_url}/webdav/test_dir")
    assert response.status_code == 204
    assert response.headers["DAV"] == "1,2"


@pytest.mark.asyncio
async def test_webdav_propfind_root(client, project_config, project_url):
    """Test WebDAV PROPFIND for project root directory."""
    # Create some test files and directories
    (project_config.home / "test.md").write_text("# Test")
    (project_config.home / "subdir").mkdir(exist_ok=True)
    (project_config.home / "subdir" / "nested.md").write_text("# Nested")

    # Test PROPFIND request for root
    response = await client.request("PROPFIND", f"{project_url}/webdav/")
    assert response.status_code == 207
    assert response.headers["content-type"] == "text/xml; charset=utf-8"

    # Check XML response contains directory listing
    xml_content = response.text
    assert "<?xml version=" in xml_content
    assert "<D:multistatus" in xml_content
    assert "test.md" in xml_content
    assert "subdir" in xml_content
    assert "<D:collection/>" in xml_content  # For directory


@pytest.mark.asyncio
async def test_webdav_propfind_subdirectory(client, project_config, project_url):
    """Test WebDAV PROPFIND for a subdirectory."""
    # Create test structure
    subdir = project_config.home / "docs"
    subdir.mkdir(exist_ok=True)
    (subdir / "readme.md").write_text("# README")
    (subdir / "guide.md").write_text("# Guide")

    # Test PROPFIND request for subdirectory
    response = await client.request("PROPFIND", f"{project_url}/webdav/docs")
    assert response.status_code == 207

    xml_content = response.text
    assert "readme.md" in xml_content
    assert "guide.md" in xml_content
    assert "<D:getcontentlength>" in xml_content  # File size information


@pytest.mark.asyncio
async def test_webdav_propfind_file(client, project_config, project_url):
    """Test WebDAV PROPFIND for a single file."""
    # Create a test file
    test_file = project_config.home / "single.md"
    test_file.write_text("# Single File")

    # Test PROPFIND request for the file
    response = await client.request("PROPFIND", f"{project_url}/webdav/single.md")
    assert response.status_code == 207

    xml_content = response.text
    assert "single.md" in xml_content
    assert "<D:getcontentlength>" in xml_content
    assert "<D:resourcetype/>" in xml_content  # Empty for files


@pytest.mark.asyncio
async def test_webdav_get_file(client, project_config, project_url):
    """Test WebDAV GET for downloading a file."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file for download."
    test_file = project_config.home / "download.md"
    test_file.write_text(content)

    # Test GET request
    response = await client.get(f"{project_url}/webdav/download.md")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert "Content-Length" in response.headers
    # Normalize line endings for cross-platform compatibility
    assert content.replace("\n", "\r\n") in response.text or content in response.text


@pytest.mark.asyncio
async def test_webdav_get_binary_file(client, project_config, project_url):
    """Test WebDAV GET for downloading a binary file."""
    # Create a test binary file
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    test_file = project_config.home / "test.png"
    test_file.write_bytes(binary_content)

    # Test GET request
    response = await client.get(f"{project_url}/webdav/test.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == binary_content


@pytest.mark.asyncio
async def test_webdav_put_new_file(client, project_config, project_url):
    """Test WebDAV PUT for creating a new file."""
    # Test data
    content = "# New File\n\nThis file was uploaded via WebDAV PUT."
    file_path = "uploads/new.md"

    # Ensure the file doesn't exist
    full_path = project_config.home / file_path
    if full_path.exists():
        full_path.unlink()

    # Test PUT request
    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={"Content-Type": "text/markdown"},
    )
    assert response.status_code == 201  # Created

    # Verify file was created
    assert full_path.exists()
    assert full_path.read_text() == content


@pytest.mark.asyncio
async def test_webdav_put_update_existing_file(client, project_config, project_url):
    """Test WebDAV PUT for updating an existing file."""
    # Create initial file
    file_path = "updates/existing.md"
    full_path = project_config.home / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    initial_content = "# Original Content"
    full_path.write_text(initial_content)

    # Update with new content
    updated_content = "# Updated Content\n\nThis content was updated via PUT."
    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=updated_content,
        headers={"Content-Type": "text/markdown"},
    )
    assert response.status_code == 204  # No Content (updated)

    # Verify file was updated
    assert full_path.read_text() == updated_content


@pytest.mark.asyncio
async def test_webdav_put_binary_file(client, project_config, project_url):
    """Test WebDAV PUT for uploading a binary file."""
    # Test binary data
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    file_path = "images/uploaded.png"

    # Test PUT request with binary data
    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=binary_content,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 201

    # Verify binary file was created correctly
    full_path = project_config.home / file_path
    assert full_path.exists()
    assert full_path.read_bytes() == binary_content


@pytest.mark.asyncio
async def test_webdav_put_nested_directory_creation(client, project_config, project_url):
    """Test WebDAV PUT creates parent directories automatically."""
    # Test creating a file in a nested path that doesn't exist
    content = "# Deep nested file"
    file_path = "very/deep/nested/path/file.md"

    # Test PUT request
    response = await client.put(f"{project_url}/webdav/{file_path}", content=content)
    assert response.status_code == 201

    # Verify directory structure was created
    full_path = project_config.home / file_path
    assert full_path.exists()
    assert full_path.read_text() == content
    assert full_path.parent.exists()


@pytest.mark.asyncio
async def test_webdav_delete_file(client, project_config, project_url):
    """Test WebDAV DELETE for removing a file."""
    # Create a test file
    file_path = "delete_me.md"
    full_path = project_config.home / file_path
    full_path.write_text("# File to delete")

    # Verify file exists
    assert full_path.exists()

    # Test DELETE request
    response = await client.delete(f"{project_url}/webdav/{file_path}")
    assert response.status_code == 204

    # Verify file was deleted
    assert not full_path.exists()


@pytest.mark.asyncio
async def test_webdav_delete_directory(client, project_config, project_url):
    """Test WebDAV DELETE for removing a directory."""
    # Create a test directory with content
    dir_path = "delete_dir"
    full_path = project_config.home / dir_path
    full_path.mkdir(exist_ok=True)
    (full_path / "file1.md").write_text("# File 1")
    (full_path / "file2.md").write_text("# File 2")

    # Verify directory exists
    assert full_path.exists()
    assert full_path.is_dir()

    # Test DELETE request
    response = await client.delete(f"{project_url}/webdav/{dir_path}")
    assert response.status_code == 204

    # Verify directory was deleted
    assert not full_path.exists()


@pytest.mark.asyncio
async def test_webdav_mkcol_create_directory(client, project_config, project_url):
    """Test WebDAV MKCOL for creating a directory."""
    # Test directory path
    dir_path = "new_collection"
    full_path = project_config.home / dir_path

    # Ensure directory doesn't exist
    if full_path.exists():
        full_path.rmdir()

    # Test MKCOL request
    response = await client.request("MKCOL", f"{project_url}/webdav/{dir_path}")
    assert response.status_code == 201

    # Verify directory was created
    assert full_path.exists()
    assert full_path.is_dir()


@pytest.mark.asyncio
async def test_webdav_mkcol_create_nested_directory(client, project_config, project_url):
    """Test WebDAV MKCOL for creating nested directories."""
    # Test nested directory path
    dir_path = "nested/directory/structure"
    full_path = project_config.home / dir_path

    # Test MKCOL request
    response = await client.request("MKCOL", f"{project_url}/webdav/{dir_path}")
    assert response.status_code == 201

    # Verify nested directory structure was created
    assert full_path.exists()
    assert full_path.is_dir()


@pytest.mark.asyncio
async def test_webdav_mkcol_existing_directory(client, project_config, project_url):
    """Test WebDAV MKCOL for directory that already exists."""
    # Create directory first
    dir_path = "existing_dir"
    full_path = project_config.home / dir_path
    full_path.mkdir(exist_ok=True)

    # Test MKCOL request on existing directory
    response = await client.request("MKCOL", f"{project_url}/webdav/{dir_path}")
    assert response.status_code == 405  # Method Not Allowed


# Error cases


@pytest.mark.asyncio
async def test_webdav_get_nonexistent_file(client, project_url):
    """Test WebDAV GET for file that doesn't exist."""
    response = await client.get(f"{project_url}/webdav/nonexistent.md")
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


@pytest.mark.asyncio
async def test_webdav_propfind_nonexistent_path(client, project_url):
    """Test WebDAV PROPFIND for path that doesn't exist."""
    response = await client.request("PROPFIND", f"{project_url}/webdav/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_webdav_delete_nonexistent_file(client, project_url):
    """Test WebDAV DELETE for file that doesn't exist."""
    response = await client.delete(f"{project_url}/webdav/nonexistent.md")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_webdav_options_nonexistent_file(client, project_url):
    """Test WebDAV OPTIONS for file that doesn't exist."""
    response = await client.request("OPTIONS", f"{project_url}/webdav/nonexistent.md")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_webdav_nonexistent_project(client):
    """Test WebDAV endpoints with nonexistent project."""
    response = await client.get("/nonexistent-project/webdav/test.md")
    assert response.status_code == 404
    assert "Project" in response.json()["detail"]


# Integration tests


@pytest.mark.asyncio
async def test_webdav_full_upload_workflow(client, project_config, project_url):
    """Test complete workflow: create directory, upload file, verify, download."""
    # Step 1: Create directory
    response = await client.request("MKCOL", f"{project_url}/webdav/workflow")
    assert response.status_code == 201

    # Step 2: Upload file
    content = "# Workflow Test\n\nComplete WebDAV workflow test."
    response = await client.put(f"{project_url}/webdav/workflow/test.md", content=content)
    assert response.status_code == 201

    # Step 3: Verify with PROPFIND
    response = await client.request("PROPFIND", f"{project_url}/webdav/workflow")
    assert response.status_code == 207
    assert "test.md" in response.text

    # Step 4: Download and verify content
    response = await client.get(f"{project_url}/webdav/workflow/test.md")
    assert response.status_code == 200
    assert response.text == content

    # Step 5: Update file
    updated_content = content + "\n\nUpdated content."
    response = await client.put(f"{project_url}/webdav/workflow/test.md", content=updated_content)
    assert response.status_code == 204

    # Step 6: Verify update
    response = await client.get(f"{project_url}/webdav/workflow/test.md")
    assert response.status_code == 200
    assert response.text == updated_content

    # Step 7: Clean up
    response = await client.delete(f"{project_url}/webdav/workflow/test.md")
    assert response.status_code == 204

    response = await client.delete(f"{project_url}/webdav/workflow")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_webdav_mixed_content_types(client, project_config, project_url):
    """Test WebDAV with various file types."""
    # Test files of different types
    test_files = {
        "document.md": "# Markdown Document",
        "data.json": json.dumps({"key": "value"}, indent=2),
        "config.txt": "key=value\nother=setting",
        "binary.dat": b"\x00\x01\x02\x03\xff",
    }

    # Upload all files
    for filename, content in test_files.items():
        if isinstance(content, bytes):
            response = await client.put(f"{project_url}/webdav/{filename}", content=content)
        else:
            response = await client.put(f"{project_url}/webdav/{filename}", content=content)
        assert response.status_code == 201

    # Verify all files with PROPFIND
    response = await client.request("PROPFIND", f"{project_url}/webdav/")
    assert response.status_code == 207
    for filename in test_files.keys():
        assert filename in response.text

    # Download and verify each file
    for filename, original_content in test_files.items():
        response = await client.get(f"{project_url}/webdav/{filename}")
        assert response.status_code == 200

        if isinstance(original_content, bytes):
            assert response.content == original_content
        else:
            assert response.text == original_content


# Timestamp preservation tests


@pytest.mark.asyncio
async def test_webdav_put_with_xoc_mtime_header(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamps from X-OC-Mtime header."""
    # Test data
    content = "# Test File\n\nTesting timestamp preservation."
    file_path = "timestamps/xoc_mtime.md"

    # Set a specific timestamp (Jan 1, 2020 12:00:00 UTC)
    target_timestamp = 1577880000.0

    # Test PUT request with X-OC-Mtime header
    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={"X-OC-Mtime": str(target_timestamp)},
    )
    assert response.status_code == 201

    # Verify file was created and timestamp was preserved
    full_path = project_config.home / file_path
    assert full_path.exists()

    stat = full_path.stat()
    # Allow small tolerance for timestamp precision
    assert abs(stat.st_mtime - target_timestamp) < 1.0


@pytest.mark.asyncio
async def test_webdav_put_with_x_timestamp_header(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamps from X-Timestamp header."""
    content = "# Test File\n\nTesting X-Timestamp header."
    file_path = "timestamps/x_timestamp.md"

    # Set a specific timestamp (Dec 25, 2021 08:30:45 UTC)
    target_timestamp = 1640422245.0

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={"X-Timestamp": str(target_timestamp)},
    )
    assert response.status_code == 201

    full_path = project_config.home / file_path
    stat = full_path.stat()
    assert abs(stat.st_mtime - target_timestamp) < 1.0


@pytest.mark.asyncio
async def test_webdav_put_with_x_mtime_header(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamps from X-Mtime header."""
    content = "# Test File\n\nTesting X-Mtime header."
    file_path = "timestamps/x_mtime.md"

    # Set a specific timestamp (July 4, 2022 15:45:30 UTC)
    target_timestamp = 1656946530.0

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={"X-Mtime": str(target_timestamp)},
    )
    assert response.status_code == 201

    full_path = project_config.home / file_path
    stat = full_path.stat()
    assert abs(stat.st_mtime - target_timestamp) < 1.0


@pytest.mark.asyncio
async def test_webdav_put_with_last_modified_header(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamps from Last-Modified header."""
    content = "# Test File\n\nTesting Last-Modified header."
    file_path = "timestamps/last_modified.md"

    # HTTP date format timestamp (March 15, 2023 10:20:30 GMT)
    last_modified_str = "Wed, 15 Mar 2023 10:20:30 GMT"
    # Expected timestamp (correct calculation)
    target_timestamp = 1678875630.0

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={"Last-Modified": last_modified_str},
    )
    assert response.status_code == 201

    full_path = project_config.home / file_path
    stat = full_path.stat()
    assert abs(stat.st_mtime - target_timestamp) < 1.0


@pytest.mark.asyncio
async def test_webdav_put_without_timestamp_headers(client, project_config, project_url):
    """Test WebDAV PUT uses current time when no timestamp headers provided."""
    content = "# Test File\n\nNo timestamp headers."
    file_path = "timestamps/no_headers.md"

    # Record current time before upload
    before_upload = time.time()

    response = await client.put(f"{project_url}/webdav/{file_path}", content=content)
    assert response.status_code == 201

    # Record current time after upload
    after_upload = time.time()

    full_path = project_config.home / file_path
    stat = full_path.stat()

    # File timestamp should be between before and after upload times
    # Allow small tolerance for file system timestamp precision differences
    assert before_upload - 0.1 <= stat.st_mtime <= after_upload + 0.1


@pytest.mark.asyncio
async def test_webdav_put_header_priority(client, project_config, project_url):
    """Test header priority: X-OC-Mtime takes precedence over others."""
    content = "# Test File\n\nTesting header priority."
    file_path = "timestamps/priority_test.md"

    # Set multiple timestamp headers
    xoc_timestamp = 1577880000.0  # Jan 1, 2020
    x_timestamp = 1640422245.0  # Dec 25, 2021
    last_modified = "Wed, 15 Mar 2023 10:20:30 GMT"  # March 15, 2023

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={
            "X-OC-Mtime": str(xoc_timestamp),
            "X-Timestamp": str(x_timestamp),
            "Last-Modified": last_modified,
        },
    )
    assert response.status_code == 201

    full_path = project_config.home / file_path
    stat = full_path.stat()
    # Should use X-OC-Mtime (highest priority)
    assert abs(stat.st_mtime - xoc_timestamp) < 1.0


@pytest.mark.asyncio
async def test_webdav_put_invalid_timestamp_headers(client, project_config, project_url):
    """Test WebDAV PUT handles invalid timestamp headers gracefully."""
    content = "# Test File\n\nTesting invalid timestamps."
    file_path = "timestamps/invalid_headers.md"

    before_upload = time.time()

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content,
        headers={
            "X-OC-Mtime": "not_a_number",
            "X-Timestamp": "invalid",
            "Last-Modified": "Not a valid date format",
        },
    )
    assert response.status_code == 201

    after_upload = time.time()

    full_path = project_config.home / file_path
    stat = full_path.stat()

    # Should fall back to current time when all headers are invalid
    # Allow small tolerance for file system timestamp precision differences
    assert before_upload - 0.1 <= stat.st_mtime <= after_upload + 0.1


@pytest.mark.asyncio
async def test_webdav_put_update_preserves_timestamp(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamp when updating existing file."""
    content1 = "# Original Content"
    content2 = "# Updated Content"
    file_path = "timestamps/update_test.md"

    # Create file with specific timestamp
    original_timestamp = 1577880000.0  # Jan 1, 2020

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content1,
        headers={"X-OC-Mtime": str(original_timestamp)},
    )
    assert response.status_code == 201

    # Update file with different timestamp
    updated_timestamp = 1640422245.0  # Dec 25, 2021

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=content2,
        headers={"X-OC-Mtime": str(updated_timestamp)},
    )
    assert response.status_code == 204  # Updated existing file

    full_path = project_config.home / file_path
    stat = full_path.stat()

    # Should have the updated timestamp
    assert abs(stat.st_mtime - updated_timestamp) < 1.0
    # Content should be updated
    assert full_path.read_text() == content2


@pytest.mark.asyncio
async def test_webdav_put_binary_file_with_timestamp(client, project_config, project_url):
    """Test WebDAV PUT preserves timestamps for binary files."""
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    file_path = "timestamps/binary_with_timestamp.png"

    target_timestamp = 1656946530.0  # July 4, 2022

    response = await client.put(
        f"{project_url}/webdav/{file_path}",
        content=binary_content,
        headers={"X-OC-Mtime": str(target_timestamp), "Content-Type": "image/png"},
    )
    assert response.status_code == 201

    full_path = project_config.home / file_path
    stat = full_path.stat()
    assert abs(stat.st_mtime - target_timestamp) < 1.0
    assert full_path.read_bytes() == binary_content


@pytest.mark.asyncio
async def test_webdav_timestamp_integration_workflow(client, project_config, project_url):
    """Test complete workflow with timestamp preservation."""
    # Create multiple files with different timestamps to simulate project upload
    files_data = [
        {
            "path": "notes/readme.md",
            "content": "# Project README",
            "timestamp": 1577880000.0,  # Jan 1, 2020
        },
        {
            "path": "notes/changelog.md",
            "content": "# Changelog\n\n## v1.0.0",
            "timestamp": 1640422245.0,  # Dec 25, 2021
        },
        {
            "path": "docs/guide.md",
            "content": "# User Guide",
            "timestamp": 1656946530.0,  # July 4, 2022
        },
    ]

    # Upload all files with their original timestamps
    for file_data in files_data:
        response = await client.put(
            f"{project_url}/webdav/{file_data['path']}",
            content=file_data["content"],
            headers={"X-OC-Mtime": str(file_data["timestamp"])},
        )
        assert response.status_code == 201

    # Verify all files have correct timestamps and content
    for file_data in files_data:
        full_path = project_config.home / file_data["path"]
        assert full_path.exists()
        assert full_path.read_text() == file_data["content"]

        stat = full_path.stat()
        assert abs(stat.st_mtime - file_data["timestamp"]) < 1.0

    # Verify directory structure
    response = await client.request("PROPFIND", f"{project_url}/webdav/")
    assert response.status_code == 207
    assert "notes" in response.text
    assert "docs" in response.text

    # Verify file listing in subdirectories
    response = await client.request("PROPFIND", f"{project_url}/webdav/notes")
    assert response.status_code == 207
    assert "readme.md" in response.text
    assert "changelog.md" in response.text
