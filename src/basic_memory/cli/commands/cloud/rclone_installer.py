"""Cross-platform rclone installation utilities."""

import os
import platform
import shutil
import subprocess
from typing import Optional

from rich.console import Console

console = Console()


class RcloneInstallError(Exception):
    """Exception raised for rclone installation errors."""

    pass


def is_rclone_installed() -> bool:
    """Check if rclone is already installed and available in PATH."""
    return shutil.which("rclone") is not None


def get_platform() -> str:
    """Get the current platform identifier."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    elif system == "windows":
        return "windows"
    else:
        raise RcloneInstallError(f"Unsupported platform: {system}")


def run_command(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command with proper error handling."""
    try:
        console.print(f"[dim]Running: {' '.join(command)}[/dim]")
        result = subprocess.run(command, capture_output=True, text=True, check=check)
        if result.stdout:
            console.print(f"[dim]Output: {result.stdout.strip()}[/dim]")
        return result
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Command failed: {e}[/red]")
        if e.stderr:
            console.print(f"[red]Error output: {e.stderr}[/red]")
        raise RcloneInstallError(f"Command failed: {e}") from e
    except FileNotFoundError as e:
        raise RcloneInstallError(f"Command not found: {' '.join(command)}") from e


def install_rclone_macos() -> None:
    """Install rclone on macOS using Homebrew or official script."""
    # Try Homebrew first
    if shutil.which("brew"):
        try:
            console.print("[blue]Installing rclone via Homebrew...[/blue]")
            run_command(["brew", "install", "rclone"])
            console.print("[green]rclone installed via Homebrew[/green]")
            return
        except RcloneInstallError:
            console.print(
                "[yellow]Homebrew installation failed, trying official script...[/yellow]"
            )

    # Fallback to official script
    console.print("[blue]Installing rclone via official script...[/blue]")
    try:
        run_command(["sh", "-c", "curl https://rclone.org/install.sh | sudo bash"])
        console.print("[green]rclone installed via official script[/green]")
    except RcloneInstallError:
        raise RcloneInstallError(
            "Failed to install rclone. Please install manually: brew install rclone"
        )


def install_rclone_linux() -> None:
    """Install rclone on Linux using package managers or official script."""
    # Try snap first (most universal)
    if shutil.which("snap"):
        try:
            console.print("[blue]Installing rclone via snap...[/blue]")
            run_command(["sudo", "snap", "install", "rclone"])
            console.print("[green]rclone installed via snap[/green]")
            return
        except RcloneInstallError:
            console.print("[yellow]Snap installation failed, trying apt...[/yellow]")

    # Try apt (Debian/Ubuntu)
    if shutil.which("apt"):
        try:
            console.print("[blue]Installing rclone via apt...[/blue]")
            run_command(["sudo", "apt", "update"])
            run_command(["sudo", "apt", "install", "-y", "rclone"])
            console.print("[green]rclone installed via apt[/green]")
            return
        except RcloneInstallError:
            console.print("[yellow]apt installation failed, trying official script...[/yellow]")

    # Fallback to official script
    console.print("[blue]Installing rclone via official script...[/blue]")
    try:
        run_command(["sh", "-c", "curl https://rclone.org/install.sh | sudo bash"])
        console.print("[green]rclone installed via official script[/green]")
    except RcloneInstallError:
        raise RcloneInstallError(
            "Failed to install rclone. Please install manually: sudo snap install rclone"
        )


def install_rclone_windows() -> None:
    """Install rclone on Windows using package managers."""
    # Try winget first (built into Windows 10+)
    if shutil.which("winget"):
        try:
            console.print("[blue]Installing rclone via winget...[/blue]")
            run_command(
                [
                    "winget",
                    "install",
                    "Rclone.Rclone",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ]
            )
            console.print("[green]rclone installed via winget[/green]")
            return
        except RcloneInstallError:
            console.print("[yellow]winget installation failed, trying chocolatey...[/yellow]")

    # Try chocolatey
    if shutil.which("choco"):
        try:
            console.print("[blue]Installing rclone via chocolatey...[/blue]")
            run_command(["choco", "install", "rclone", "-y"])
            console.print("[green]rclone installed via chocolatey[/green]")
            return
        except RcloneInstallError:
            console.print("[yellow]chocolatey installation failed, trying scoop...[/yellow]")

    # Try scoop
    if shutil.which("scoop"):
        try:
            console.print("[blue]Installing rclone via scoop...[/blue]")
            run_command(["scoop", "install", "rclone"])
            console.print("[green]rclone installed via scoop[/green]")
            return
        except RcloneInstallError:
            console.print("[yellow]scoop installation failed[/yellow]")

    # No package manager available - provide detailed instructions
    error_msg = (
        "Could not install rclone automatically.\n\n"
        "Windows requires a package manager to install rclone. Options:\n\n"
        "1. Install winget (recommended, built into Windows 11):\n"
        "   - Windows 11: Already installed\n"
        "   - Windows 10: Install 'App Installer' from Microsoft Store\n"
        "   - Then run: bm cloud setup\n\n"
        "2. Install chocolatey:\n"
        "   - Visit: https://chocolatey.org/install\n"
        "   - Then run: bm cloud setup\n\n"
        "3. Install scoop:\n"
        "   - Visit: https://scoop.sh\n"
        "   - Then run: bm cloud setup\n\n"
        "4. Manual installation:\n"
        "   - Download from: https://rclone.org/downloads/\n"
        "   - Extract and add to PATH\n"
    )
    raise RcloneInstallError(error_msg)


def install_rclone(platform_override: Optional[str] = None) -> None:
    """Install rclone for the current platform."""
    if is_rclone_installed():
        console.print("[green]rclone is already installed[/green]")
        return

    platform_name = platform_override or get_platform()
    console.print(f"[blue]Installing rclone for {platform_name}...[/blue]")

    try:
        if platform_name == "macos":
            install_rclone_macos()
        elif platform_name == "linux":
            install_rclone_linux()
        elif platform_name == "windows":
            install_rclone_windows()
            refresh_windows_path()
        else:
            raise RcloneInstallError(f"Unsupported platform: {platform_name}")

        # Verify installation
        if not is_rclone_installed():
            raise RcloneInstallError("rclone installation completed but command not found in PATH")

        console.print("[green]rclone installation completed successfully[/green]")

    except RcloneInstallError:
        raise
    except Exception as e:
        raise RcloneInstallError(f"Unexpected error during installation: {e}") from e


def refresh_windows_path() -> None:
    """Refresh the Windows PATH environment variable for the current session."""
    if platform.system().lower() != "windows":
        return

    # Importing here after performing platform detection. Also note that we have to ignore pylance/pyright
    # warnings about winreg attributes so that "errors" don't appear on non-Windows platforms.
    import winreg

    user_key_path = r"Environment"
    system_key_path = r"System\CurrentControlSet\Control\Session Manager\Environment"
    new_path = ""

    # Read user PATH
    try:
        reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_key_path, 0, winreg.KEY_READ)  # type: ignore[reportAttributeAccessIssue]
        user_path, _ = winreg.QueryValueEx(reg_key, "PATH")  # type: ignore[reportAttributeAccessIssue]
        winreg.CloseKey(reg_key)  # type: ignore[reportAttributeAccessIssue]
    except Exception:
        user_path = ""

    # Read system PATH
    try:
        reg_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, system_key_path, 0, winreg.KEY_READ)  # type: ignore[reportAttributeAccessIssue]
        system_path, _ = winreg.QueryValueEx(reg_key, "PATH")  # type: ignore[reportAttributeAccessIssue]
        winreg.CloseKey(reg_key)  # type: ignore[reportAttributeAccessIssue]
    except Exception:
        system_path = ""

    # Merge user and system PATHs (system first, then user)
    if system_path and user_path:
        new_path = system_path + ";" + user_path
    elif system_path:
        new_path = system_path
    elif user_path:
        new_path = user_path

    if new_path:
        os.environ["PATH"] = new_path


def get_rclone_version() -> Optional[str]:
    """Get the installed rclone version."""
    if not is_rclone_installed():
        return None

    try:
        result = run_command(["rclone", "version"], check=False)
        if result.returncode == 0:
            # Parse version from output (format: "rclone v1.64.0")
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if line.startswith("rclone v"):
                    return line.split()[1]
        return "unknown"
    except Exception:
        return "unknown"
