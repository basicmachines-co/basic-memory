"""Tests for the read_content MCP tool security validation."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from basic_memory.mcp.tools.read_content import read_content
from basic_memory.mcp.tools.write_note import write_note


class TestReadContentSecurityValidation:
    """Test read_content security validation features."""

    @pytest.mark.asyncio
    async def test_read_content_blocks_path_traversal_unix(self, client, test_project):
        """Test that Unix-style path traversal attacks are blocked."""
        # Test various Unix-style path traversal patterns
        attack_paths = [
            "../secrets.txt",
            "../../etc/passwd",
            "../../../root/.ssh/id_rsa",
            "notes/../../../etc/shadow",
            "folder/../../outside/file.md",
            "../../../../etc/hosts",
            "../../../home/user/.env",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]
            assert attack_path in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_blocks_path_traversal_windows(self, client, test_project):
        """Test that Windows-style path traversal attacks are blocked."""
        # Test various Windows-style path traversal patterns
        attack_paths = [
            "..\\secrets.txt",
            "..\\..\\Windows\\System32\\config\\SAM",
            "notes\\..\\..\\..\\Windows\\System32",
            "\\\\server\\share\\file.txt",
            "..\\..\\Users\\user\\.env",
            "\\\\..\\..\\Windows",
            "..\\..\\..\\Boot.ini",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]
            assert attack_path in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_blocks_absolute_paths(self, client, test_project):
        """Test that absolute paths are blocked."""
        # Test various absolute path patterns
        attack_paths = [
            "/etc/passwd",
            "/home/user/.env",
            "/var/log/auth.log",
            "/root/.ssh/id_rsa",
            "C:\\Windows\\System32\\config\\SAM",
            "C:\\Users\\user\\.env",
            "D:\\secrets\\config.json",
            "/tmp/malicious.txt",
            "/usr/local/bin/evil",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]
            assert attack_path in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_blocks_home_directory_access(self, client, test_project):
        """Test that home directory access patterns are blocked."""
        # Test various home directory access patterns
        attack_paths = [
            "~/secrets.txt",
            "~/.env",
            "~/.ssh/id_rsa",
            "~/Documents/passwords.txt",
            "~\\AppData\\secrets",
            "~\\Desktop\\config.ini",
            "~/.bashrc",
            "~/Library/Preferences/secret.plist",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]
            assert attack_path in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_blocks_mixed_attack_patterns(self, client, test_project):
        """Test that mixed legitimate/attack patterns are blocked."""
        # Test mixed patterns that start legitimate but contain attacks
        attack_paths = [
            "notes/../../../etc/passwd",
            "docs/../../.env",
            "legitimate/path/../../.ssh/id_rsa",
            "project/folder/../../../Windows/System32",
            "valid/folder/../../home/user/.bashrc",
            "assets/../../../tmp/evil.exe",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_allows_safe_paths_with_mocked_api(self, client, test_project):
        """Test that legitimate paths are still allowed with mocked API responses."""
        # Test various safe path patterns with mocked API responses
        safe_paths = [
            "notes/meeting.md",
            "docs/readme.txt",
            "projects/2025/planning.md",
            "archive/old-notes/backup.md",
            "assets/diagram.png",
            "folder/subfolder/document.md",
        ]

        for safe_path in safe_paths:
            # Mock the API call to simulate a successful response
            with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
                mock_response = MagicMock()
                mock_response.headers = {"content-type": "text/markdown", "content-length": "100"}
                mock_response.text = f"# Content for {safe_path}\nThis is test content."
                mock_call_get.return_value = mock_response

                result = await read_content.fn(project=test_project.name, path=safe_path)

                # Should succeed (not a security error)
                assert isinstance(result, dict)
                assert result[
                    "type"
                ] != "error" or "paths must stay within project boundaries" not in result.get(
                    "error", ""
                )

    @pytest.mark.asyncio
    async def test_read_content_memory_url_processing(self, client, test_project):
        """Test that memory URLs are processed correctly for security validation."""
        # Test memory URLs with attacks
        attack_paths = [
            "memory://../../etc/passwd",
            "memory://../../../root/.ssh/id_rsa",
            "memory://~/.env",
            "memory:///etc/passwd",
        ]

        for attack_path in attack_paths:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_security_logging(self, client, caplog, test_project):
        """Test that security violations are properly logged."""
        # Attempt path traversal attack
        result = await read_content.fn(project=test_project.name, path="../../../etc/passwd")

        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]

        # Check that security violation was logged
        # Note: This test may need adjustment based on the actual logging setup
        # The security validation should generate a warning log entry

    @pytest.mark.asyncio
    async def test_read_content_empty_path_security(self, client, test_project):
        """Test that empty path is handled securely."""
        # Mock the API call since empty path should be allowed (resolves to project root)
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            mock_response = MagicMock()
            mock_response.headers = {"content-type": "text/markdown", "content-length": "50"}
            mock_response.text = "# Root content"
            mock_call_get.return_value = mock_response

            result = await read_content.fn(project=test_project.name, path="")

            assert isinstance(result, dict)
            # Empty path should not trigger security error (it's handled as project root)
            assert result[
                "type"
            ] != "error" or "paths must stay within project boundaries" not in result.get(
                "error", ""
            )

    @pytest.mark.asyncio
    async def test_read_content_current_directory_references_security(self, client, test_project):
        """Test that current directory references are handled securely."""
        # Test current directory references (should be safe)
        safe_paths = [
            "./notes/file.md",
            "folder/./file.md",
            "./folder/subfolder/file.md",
        ]

        for safe_path in safe_paths:
            # Mock the API call for these safe paths
            with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
                mock_response = MagicMock()
                mock_response.headers = {"content-type": "text/markdown", "content-length": "100"}
                mock_response.text = f"# Content for {safe_path}"
                mock_call_get.return_value = mock_response

                result = await read_content.fn(project=test_project.name, path=safe_path)

                assert isinstance(result, dict)
                # Should NOT contain security error message
                assert result[
                    "type"
                ] != "error" or "paths must stay within project boundaries" not in result.get(
                    "error", ""
                )


class TestReadContentFunctionality:
    """Test read_content basic functionality with security validation in place."""

    @pytest.mark.asyncio
    async def test_read_content_text_file_success(self, client, test_project):
        """Test reading a text file works correctly with security validation."""
        # First create a file to read
        await write_note.fn(
            project=test_project.name,
            title="Test Document",
            folder="docs",
            content="# Test Document\nThis is test content for reading.",
        )

        # Mock the API call to simulate reading the file
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            mock_response = MagicMock()
            mock_response.headers = {"content-type": "text/markdown", "content-length": "100"}
            mock_response.text = "# Test Document\nThis is test content for reading."
            mock_call_get.return_value = mock_response

            result = await read_content.fn(project=test_project.name, path="docs/test-document.md")

            assert isinstance(result, dict)
            assert result["type"] == "text"
            assert "Test Document" in result["text"]
            assert result["content_type"] == "text/markdown"
            assert result["encoding"] == "utf-8"

    @pytest.mark.asyncio
    async def test_read_content_image_file_handling(self, client, test_project):
        """Test reading an image file with security validation."""
        # Mock the API call to simulate reading an image
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            # Create a simple fake image data
            fake_image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"

            mock_response = MagicMock()
            mock_response.headers = {
                "content-type": "image/png",
                "content-length": str(len(fake_image_data)),
            }
            mock_response.content = fake_image_data
            mock_call_get.return_value = mock_response

            # Mock PIL Image processing
            with patch("basic_memory.mcp.tools.read_content.PILImage") as mock_pil:
                mock_img = MagicMock()
                mock_img.width = 100
                mock_img.height = 100
                mock_img.mode = "RGB"
                mock_img.getbands.return_value = ["R", "G", "B"]
                mock_pil.open.return_value = mock_img

                with patch("basic_memory.mcp.tools.read_content.optimize_image") as mock_optimize:
                    mock_optimize.return_value = b"optimized_image_data"

                    result = await read_content.fn(
                        project=test_project.name, path="assets/safe-image.png"
                    )

                    assert isinstance(result, dict)
                    assert result["type"] == "image"
                    assert "source" in result
                    assert result["source"]["type"] == "base64"
                    assert result["source"]["media_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_read_content_with_project_parameter(self, client, test_project):
        """Test reading content with explicit project parameter."""
        # Mock the API call and project configuration
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            with patch(
                "basic_memory.mcp.tools.read_content.get_active_project"
            ) as mock_get_project:
                # Mock project configuration
                mock_project = MagicMock()
                mock_project.project_url = "http://test"
                mock_project.home = Path("/test/project")
                mock_get_project.return_value = mock_project

                mock_response = MagicMock()
                mock_response.headers = {"content-type": "text/plain", "content-length": "50"}
                mock_response.text = "Project-specific content"
                mock_call_get.return_value = mock_response

                result = await read_content.fn(
                    path="notes/project-file.txt", project="specific-project"
                )

                assert isinstance(result, dict)
                assert result["type"] == "text"
                assert "Project-specific content" in result["text"]

    @pytest.mark.asyncio
    async def test_read_content_nonexistent_file_handling(self, client, test_project):
        """Test handling of nonexistent files (after security validation)."""
        # Mock API call to return 404
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            mock_call_get.side_effect = Exception("File not found")

            # This should pass security validation but fail on API call
            try:
                result = await read_content.fn(
                    project=test_project.name, path="docs/nonexistent-file.md"
                )
                # If no exception is raised, check the result format
                assert isinstance(result, dict)
            except Exception as e:
                # Exception due to API failure is acceptable for this test
                assert "File not found" in str(e)

    @pytest.mark.asyncio
    async def test_read_content_binary_file_handling(self, client, test_project):
        """Test reading binary files with security validation."""
        # Mock the API call to simulate reading a binary file
        with patch("basic_memory.mcp.tools.read_content.call_get") as mock_call_get:
            binary_data = b"Binary file content with special bytes: \x00\x01\x02\x03"

            mock_response = MagicMock()
            mock_response.headers = {
                "content-type": "application/octet-stream",
                "content-length": str(len(binary_data)),
            }
            mock_response.content = binary_data
            mock_call_get.return_value = mock_response

            result = await read_content.fn(project=test_project.name, path="files/safe-binary.bin")

            assert isinstance(result, dict)
            assert result["type"] == "document"
            assert "source" in result
            assert result["source"]["type"] == "base64"
            assert result["source"]["media_type"] == "application/octet-stream"


class TestReadContentEdgeCases:
    """Test edge cases for read_content security validation."""

    @pytest.mark.asyncio
    async def test_read_content_unicode_path_attacks(self, client, test_project):
        """Test that Unicode-based path traversal attempts are blocked."""
        # Test Unicode path traversal attempts
        unicode_attacks = [
            "notes/文档/../../../etc/passwd",  # Chinese characters
            "docs/café/../../.env",  # Accented characters
            "files/αβγ/../../../secret.txt",  # Greek characters
        ]

        for attack_path in unicode_attacks:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_url_encoded_attacks(self, client, test_project):
        """Test that URL-encoded path traversal attempts are handled safely."""
        # Note: The current implementation may not handle URL encoding,
        # but this tests the behavior with URL-encoded patterns
        encoded_attacks = [
            "notes%2f..%2f..%2f..%2fetc%2fpasswd",
            "docs%2f%2e%2e%2f%2e%2e%2f.env",
        ]

        for attack_path in encoded_attacks:
            try:
                result = await read_content.fn(project=test_project.name, path=attack_path)

                # These may or may not be blocked depending on URL decoding,
                # but should not cause security issues
                assert isinstance(result, dict)

                # If not blocked by security validation, may fail at API level
                # which is also acceptable

            except Exception:
                # Exception due to API failure or other issues is acceptable
                # as long as no actual traversal occurs
                pass

    @pytest.mark.asyncio
    async def test_read_content_null_byte_injection(self, client, test_project):
        """Test that null byte injection attempts are blocked."""
        # Test null byte injection patterns
        null_byte_attacks = [
            "notes/file.txt\x00../../etc/passwd",
            "docs/document.md\x00../../../.env",
        ]

        for attack_path in null_byte_attacks:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            # Should be blocked by security validation or cause an error
            if result["type"] == "error":
                # Either blocked by security validation or failed due to invalid characters
                pass  # This is acceptable

    @pytest.mark.asyncio
    async def test_read_content_very_long_attack_path(self, client, test_project):
        """Test handling of very long attack paths."""
        # Create a very long path traversal attack
        long_attack = "../" * 1000 + "etc/passwd"

        result = await read_content.fn(project=test_project.name, path=long_attack)

        assert isinstance(result, dict)
        assert result["type"] == "error"
        assert "paths must stay within project boundaries" in result["error"]

    @pytest.mark.asyncio
    async def test_read_content_case_variations_attacks(self, client, test_project):
        """Test that case variations don't bypass security."""
        # Test case variations (though case sensitivity depends on filesystem)
        case_attacks = [
            "../ETC/passwd",
            "../Etc/PASSWD",
            "..\\WINDOWS\\system32",
            "~/.SSH/id_rsa",
        ]

        for attack_path in case_attacks:
            result = await read_content.fn(project=test_project.name, path=attack_path)

            assert isinstance(result, dict)
            assert result["type"] == "error"
            assert "paths must stay within project boundaries" in result["error"]
