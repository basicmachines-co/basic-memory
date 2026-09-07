"""The public manifest must identify Basic Memory, not its checkout directory."""

from pathlib import Path

from tau_coding.extensions.loader import load_extensions
from tau_coding.resources import TauResourcePaths


def test_manifest_loads_basic_memory_by_display_name(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = load_extensions(
        TauResourcePaths(root=tmp_path),
        extra_paths=[root],
        include_resource_dirs=False,
    )
    assert not result.diagnostics
    assert [extension.name for extension in result.extensions] == ["Basic Memory"]
    assert callable(result.extensions[0].setup)
