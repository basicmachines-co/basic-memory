"""Static assertions for core runtime dependency direction."""

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "basic_memory"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_core_production_modules_do_not_import_cloud_compatibility_package() -> None:
    violations = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            module for module in _imported_modules(path) if module.startswith("basic_memory.cloud")
        )
        for path in PACKAGE_ROOT.rglob("*.py")
        if "cloud" not in path.relative_to(PACKAGE_ROOT).parts
    }
    assert not {path: modules for path, modules in violations.items() if modules}


def test_repositories_do_not_import_indexing_workflows() -> None:
    repository_root = PACKAGE_ROOT / "repository"
    violations = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            module
            for module in _imported_modules(path)
            if module.startswith("basic_memory.indexing")
        )
        for path in repository_root.rglob("*.py")
    }
    assert not {path: modules for path, modules in violations.items() if modules}
