"""Tests for secure rclone installer fallbacks."""

import pytest

from basic_memory.cli.commands.cloud import rclone_installer


def test_macos_installer_does_not_fallback_to_remote_script(monkeypatch):
    """Homebrew failure should produce manual guidance, not curl-piped sudo bash."""
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return "/opt/homebrew/bin/brew" if command == "brew" else None

    def fake_run(command: list[str], check: bool = True):
        commands.append(command)
        raise rclone_installer.RcloneInstallError("brew failed")

    monkeypatch.setattr(rclone_installer.shutil, "which", fake_which)
    monkeypatch.setattr(rclone_installer, "run_command", fake_run)

    with pytest.raises(rclone_installer.RcloneInstallError) as exc_info:
        rclone_installer.install_rclone_macos()

    assert commands == [["brew", "install", "rclone"]]
    assert "curl" not in str(exc_info.value)
    assert "sudo bash" not in str(exc_info.value)
    assert "brew install rclone" in str(exc_info.value)


def test_linux_installer_uses_package_managers_only(monkeypatch):
    """Linux package-manager failures should not fall through to remote script execution."""
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in {"apt", "snap"} else None

    def fake_run(command: list[str], check: bool = True):
        commands.append(command)
        raise rclone_installer.RcloneInstallError("install failed")

    monkeypatch.setattr(rclone_installer.shutil, "which", fake_which)
    monkeypatch.setattr(rclone_installer, "run_command", fake_run)

    with pytest.raises(rclone_installer.RcloneInstallError) as exc_info:
        rclone_installer.install_rclone_linux()

    assert commands == [
        ["sudo", "apt", "update"],
        ["sudo", "snap", "install", "rclone"],
    ]
    assert all("curl" not in token for command in commands for token in command)
    assert "sudo bash" not in str(exc_info.value)
    assert "sudo apt install rclone" in str(exc_info.value)
