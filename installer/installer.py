import json
import subprocess
import sys
from pathlib import Path

# Use tkinter for GUI alerts on macOS
if sys.platform == "darwin":
    import tkinter as tk
    from tkinter import messagebox


def ensure_uv_installed():
    """Check if uv is installed, install if not."""
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Installing uv package manager...")
        subprocess.run(
            [
                "curl",
                "-LsSf",
                "https://astral.sh/uv/install.sh",
                "|",
                "sh",
            ],
            shell=True,
        )


def get_config_path():
    """Get Claude Desktop config path for current platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    elif sys.platform == "win32":
        return Path.home() / "AppData/Roaming/Claude/claude_desktop_config.json"
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


def update_claude_config():
    """Update Claude Desktop config to include basic-memory."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new
    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {"mcpServers": {}}

    # Add/update basic-memory config
    config["mcpServers"]["basic-memory"] = {
        "command": "uvx",
        "args": ["basic-memory@latest", "mcp"],
        "env": {
            "BASIC_MEMORY_ENV": "user",
            "LOGFIRE_TOKEN": "n2Fpvn34LjKYq8TdF1ZrXMgdBPXGn4HfXy6tYghZ55dB",
        },
    }

    # Write back config
    config_path.write_text(json.dumps(config, indent=2))


def print_completion_message():
    """Show completion message with helpful tips."""
    message = """Installation complete! Basic Memory is now available in Claude Desktop.

Please restart Claude Desktop for changes to take effect.

Quick Start:
1. You can run sync directly using: uvx basic-memory sync
2. Optionally, install globally with: uv pip install basic-memory

Built with ♥️ by Basic Machines."""

    if sys.platform == "darwin":
        # Show GUI message on macOS
        root = tk.Tk()
        root.withdraw()  # Hide the main window
        messagebox.showinfo("Basic Memory", message)
        root.destroy()
    else:
        # Fallback to console output
        print(message)


def main():
    print("Welcome to Basic Memory installer")
    ensure_uv_installed()
    print("Configuring Claude Desktop...")
    update_claude_config()
    print_completion_message()


if __name__ == "__main__":
    main()
