"""Tests for the validate_project_path security function."""

import pytest
from pathlib import Path

from basic_memory.utils import validate_project_path


class TestValidateProjectPathSafety:
    """Test that validate_project_path correctly identifies safe paths."""

    def test_valid_relative_paths(self, tmp_path):
        """Test that legitimate relative paths are allowed."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        safe_paths = [
            "notes/meeting.md",
            "docs/readme.txt",
            "folder/subfolder/file.txt",
            "simple-file.md",
            "research/findings-2025.md",
            "projects/basic-memory/docs.md",
            "deep/nested/directory/structure/file.txt",
            "file-with-hyphens.md",
            "file_with_underscores.txt",
            "file123.md",
            "UPPERCASE.MD",
            "MixedCase.txt",
        ]

        for path in safe_paths:
            assert validate_project_path(path, project_path), (
                f"Safe path '{path}' should be allowed"
            )

    def test_empty_and_current_directory(self, tmp_path):
        """Test handling of empty paths and current directory references."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Current directory should be safe
        assert validate_project_path(".", project_path)

        # Files in current directory should be safe
        assert validate_project_path("./file.txt", project_path)

    def test_nested_safe_paths(self, tmp_path):
        """Test deeply nested but safe paths."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        nested_paths = [
            "level1/level2/level3/level4/file.txt",
            "very/deeply/nested/directory/structure/with/many/levels/file.md",
            "a/b/c/d/e/f/g/h/i/j/file.txt",
        ]

        for path in nested_paths:
            assert validate_project_path(path, project_path), (
                f"Nested path '{path}' should be allowed"
            )


class TestValidateProjectPathAttacks:
    """Test that validate_project_path blocks path traversal attacks."""

    def test_unix_path_traversal(self, tmp_path):
        """Test that Unix-style path traversal is blocked."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        attack_paths = [
            "../",
            "../../",
            "../../../",
            "../etc/passwd",
            "../../etc/passwd",
            "../../../etc/passwd",
            "../../../../etc/passwd",
            "../../.env",
            "../../../home/user/.ssh/id_rsa",
            "../../../../var/log/auth.log",
            "../../.bashrc",
            "../../../etc/shadow",
        ]

        for path in attack_paths:
            assert not validate_project_path(path, project_path), (
                f"Attack path '{path}' should be blocked"
            )

    def test_windows_path_traversal(self, tmp_path):
        """Test that Windows-style path traversal is blocked."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        attack_paths = [
            "..\\",
            "..\\..\\",
            "..\\..\\..\\",
            "..\\..\\..\\Windows\\System32\\config\\SAM",
            "..\\..\\..\\Users\\user\\.env",
            "..\\..\\..\\Windows\\System32\\drivers\\etc\\hosts",
            "..\\..\\Boot.ini",
            "\\Windows\\System32",
            "\\..\\..\\Windows",
        ]

        for path in attack_paths:
            assert not validate_project_path(path, project_path), (
                f"Windows attack path '{path}' should be blocked"
            )

    def test_mixed_traversal_patterns(self, tmp_path):
        """Test paths that mix legitimate content with traversal."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        mixed_attacks = [
            "notes/../../../etc/passwd",
            "docs/../../.env",
            "folder/subfolder/../../../etc/passwd",
            "legitimate/path/../../.ssh/id_rsa",
            "notes/../../../home/user/.bashrc",
            "documents/../../Windows/System32/config/SAM",
        ]

        for path in mixed_attacks:
            assert not validate_project_path(path, project_path), (
                f"Mixed attack path '{path}' should be blocked"
            )

    def test_home_directory_access(self, tmp_path):
        """Test that home directory access patterns are blocked."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        home_attacks = [
            "~/",
            "~/.env",
            "~/.ssh/id_rsa",
            "~/secrets.txt",
            "~/Documents/passwords.txt",
            "~\\AppData\\secrets",
            "~\\Desktop\\config.ini",
        ]

        for path in home_attacks:
            assert not validate_project_path(path, project_path), (
                f"Home directory attack '{path}' should be blocked"
            )

    def test_unc_and_network_paths(self, tmp_path):
        """Test that UNC and network paths are blocked."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        network_attacks = [
            "\\\\server\\share",
            "\\\\192.168.1.100\\c$",
            "\\\\evil-server\\malicious-share\\file.exe",
            "\\\\localhost\\c$\\Windows\\System32",
        ]

        for path in network_attacks:
            assert not validate_project_path(path, project_path), (
                f"Network path attack '{path}' should be blocked"
            )

    def test_absolute_paths(self, tmp_path):
        """Test that absolute paths are blocked (if they contain traversal)."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Note: Some absolute paths might be allowed by pathlib resolution,
        # but our function should catch traversal patterns first
        absolute_attacks = [
            "/etc/passwd",
            "/home/user/.env",
            "/var/log/auth.log",
            "/root/.ssh/id_rsa",
            "C:\\Windows\\System32\\config\\SAM",
            "C:\\Users\\user\\.env",
            "D:\\secrets\\config.json",
        ]

        for path in absolute_attacks:
            # These should be blocked either by traversal detection or pathlib resolution
            result = validate_project_path(path, project_path)
            assert not result, f"Absolute path '{path}' should be blocked"


class TestValidateProjectPathEdgeCases:
    """Test edge cases and error conditions."""

    def test_malformed_paths(self, tmp_path):
        """Test handling of malformed or unusual paths."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        malformed_paths = [
            "",  # Empty string
            "   ",  # Whitespace only
            "\n",  # Newline
            "\t",  # Tab
            "\r\n",  # Windows line ending
            "file\x00name",  # Null byte (if it gets this far)
            "file\x01name",  # Other control characters
        ]

        for path in malformed_paths:
            # These should either be blocked or cause an exception that's handled
            try:
                result = validate_project_path(path, project_path)
                if path.strip():  # Non-empty paths with control chars should be blocked
                    assert not result, f"Malformed path '{repr(path)}' should be blocked"
            except (ValueError, OSError):
                # It's acceptable for these to raise exceptions
                pass

    def test_very_long_paths(self, tmp_path):
        """Test handling of very long paths."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Create a very long but legitimate path
        long_path = "/".join(["verylongdirectoryname" * 10 for _ in range(10)])

        # Should handle long paths gracefully (either allow or reject based on filesystem limits)
        try:
            result = validate_project_path(long_path, project_path)
            # Result can be True or False, just shouldn't crash
            assert isinstance(result, bool)
        except (ValueError, OSError):
            # It's acceptable for very long paths to raise exceptions
            pass

    def test_nonexistent_project_path(self):
        """Test behavior when project path doesn't exist."""
        nonexistent_project = Path("/this/path/does/not/exist")

        # Should still be able to validate relative paths
        assert validate_project_path("notes/file.txt", nonexistent_project)
        assert not validate_project_path("../../../etc/passwd", nonexistent_project)

    def test_unicode_and_special_characters(self, tmp_path):
        """Test paths with Unicode and special characters."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        unicode_paths = [
            "notes/文档.md",  # Chinese characters
            "docs/résumé.txt",  # Accented characters
            "files/naïve.md",  # Diaeresis
            "notes/café.txt",  # Acute accent
            "docs/日本語.md",  # Japanese
            "files/αβγ.txt",  # Greek
            "notes/файл.md",  # Cyrillic
        ]

        for path in unicode_paths:
            try:
                result = validate_project_path(path, project_path)
                assert isinstance(result, bool), f"Unicode path '{path}' should return boolean"
                # Unicode paths should generally be allowed if they don't contain traversal
                assert result, f"Unicode path '{path}' should be allowed"
            except (UnicodeError, OSError):
                # Some unicode handling issues might be acceptable
                pass

    def test_case_sensitivity(self, tmp_path):
        """Test case sensitivity of traversal detection."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # These should all be blocked regardless of case
        case_variations = [
            "../file.txt",
            "../FILE.TXT",
            "~/file.txt",
            "~/FILE.TXT",
        ]

        for path in case_variations:
            assert not validate_project_path(path, project_path), (
                f"Case variation '{path}' should be blocked"
            )

    def test_symbolic_link_behavior(self, tmp_path):
        """Test behavior with symbolic links (if supported by filesystem)."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Create a directory outside the project
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        try:
            # Try to create a symlink inside the project pointing outside
            symlink_path = project_path / "symlink"
            symlink_path.symlink_to(outside_dir)

            # Paths through symlinks should be handled safely
            result = validate_project_path("symlink/file.txt", project_path)
            # The result can vary based on how pathlib handles symlinks,
            # but it shouldn't crash and should be a boolean
            assert isinstance(result, bool)

        except (OSError, NotImplementedError):
            # Symlinks might not be supported on this filesystem
            pytest.skip("Symbolic links not supported on this filesystem")

    def test_relative_path_edge_cases(self, tmp_path):
        """Test edge cases in relative path handling."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        edge_cases = [
            ".",  # Current directory
            "./",  # Current directory with slash
            "./file.txt",  # File in current directory
            "./folder/file.txt",  # Nested file through current directory
            "folder/./file.txt",  # Current directory in middle of path
            "folder/subfolder/.",  # Current directory at end
        ]

        for path in edge_cases:
            result = validate_project_path(path, project_path)
            # These should generally be safe as they don't escape the project
            assert result, f"Relative path edge case '{path}' should be allowed"


class TestValidateProjectPathPerformance:
    """Test performance characteristics of path validation."""

    def test_performance_with_many_paths(self, tmp_path):
        """Test that validation performs reasonably with many paths."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Test a mix of safe and dangerous paths
        test_paths = []

        # Add safe paths
        for i in range(100):
            test_paths.append(f"folder{i}/file{i}.txt")

        # Add dangerous paths
        for i in range(100):
            test_paths.append(f"../../../etc/passwd{i}")

        import time

        start_time = time.time()

        for path in test_paths:
            result = validate_project_path(path, project_path)
            assert isinstance(result, bool)

        end_time = time.time()

        # Should complete reasonably quickly (adjust threshold as needed)
        assert end_time - start_time < 1.0, "Path validation should be fast"


class TestValidateProjectPathIntegration:
    """Integration tests with real filesystem scenarios."""

    def test_with_actual_filesystem_structure(self, tmp_path):
        """Test validation with actual files and directories."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        # Create some actual files and directories
        (project_path / "notes").mkdir()
        (project_path / "docs").mkdir()
        (project_path / "notes" / "meeting.md").write_text("# Meeting Notes")
        (project_path / "docs" / "readme.txt").write_text("README")

        # Test accessing existing files
        assert validate_project_path("notes/meeting.md", project_path)
        assert validate_project_path("docs/readme.txt", project_path)

        # Test accessing non-existent but safe paths
        assert validate_project_path("notes/new-file.md", project_path)
        assert validate_project_path("new-folder/file.txt", project_path)

        # Test that attacks are still blocked even with real filesystem
        assert not validate_project_path("../../../etc/passwd", project_path)
        assert not validate_project_path("notes/../../../etc/passwd", project_path)

    def test_project_path_resolution_accuracy(self, tmp_path):
        """Test that path resolution works correctly with real paths."""
        # Create a more complex directory structure
        base_path = tmp_path / "workspace"
        project_path = base_path / "my-project"
        sibling_path = base_path / "other-project"

        base_path.mkdir()
        project_path.mkdir()
        sibling_path.mkdir()

        # Create a sensitive file in the sibling directory
        (sibling_path / "secrets.txt").write_text("secret data")

        # Try to access the sibling directory through traversal
        attack_path = "../other-project/secrets.txt"
        assert not validate_project_path(attack_path, project_path)

        # Verify that legitimate access within project works
        assert validate_project_path("my-file.txt", project_path)
        assert validate_project_path("subdir/my-file.txt", project_path)
