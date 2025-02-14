from cx_Freeze import setup, Executable
import sys

# Build options for all platforms
build_exe_options = {
    "packages": ["json", "pathlib"],
    "excludes": [],
}

# Platform-specific options
if sys.platform == "win32":
    base = "Win32GUI"  # Use GUI base for Windows
    build_exe_options.update({
        "include_msvcr": True,  # Include Visual C++ runtime
    })
    target_name = "Basic Memory Installer.exe"
    icon = None  # We'll add Windows icon later
else:  # darwin
    base = None  # Don't use GUI base for macOS
    target_name = "Basic Memory Installer"
    icon = "Basic.icns"

executables = [
    Executable(
        script="installer.py",
        target_name=target_name,
        base=base,
        icon=icon
    )
]

setup(
    name="basic-memory",
    version=open("../pyproject.toml").read().split('version = "', 1)[1].split('"', 1)[0],
    description="Basic Memory - Local-first knowledge management",
    options={
        "build_exe": build_exe_options,
        "bdist_mac": {
            "bundle_name": "Basic Memory Installer",
            "iconfile": icon if sys.platform == "darwin" else None
        }
    },
    executables=executables,
)