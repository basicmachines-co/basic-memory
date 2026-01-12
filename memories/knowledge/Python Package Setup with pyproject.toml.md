---
title: Python Package Setup with pyproject.toml
type: note
permalink: knowledge/python-package-setup-with-pyproject-toml
---

# Python Package Setup with pyproject.toml

Understanding how Python packages are configured and made importable.

## Key Configuration File

**pyproject.toml** is the Python equivalent of package.json

### Essential Sections

**Project Metadata:**
- name = "basic-memory" (package name with dashes)
- Imports use underscores: basic-memory → import basic_memory
- version, dependencies, etc.

**CLI Entry Points:**
```toml
[project.scripts]
basic-memory = "basic_memory.cli.main:app"
bm = "basic_memory.cli.main:app"
```

This creates CLI commands that run when you type basic-memory or bm in terminal.

## Directory Structure

The src/ layout (modern best practice):

```
project/
  src/
    my_package/    ← Actual importable package
      __init__.py  ← Makes it a package
  tests/
  pyproject.toml   ← Package configuration
```

### Why src/ Layout?
Prevents accidentally importing from source instead of installed package.

## Making Package Importable

**Development Install:**
```bash
pip install -e .
# or
uv pip install -e .
```

This:
1. Reads pyproject.toml
2. Finds [project] name
3. Looks for src/package_name/ folder
4. Creates symlink so imports work
5. Changes to source code are instantly available

## Package Resolution

When Python imports basic_memory:
1. Checks installed packages
2. Finds link to /path/to/project/src/basic_memory/
3. Loads __init__.py from there
4. Makes all submodules available

## Key Points

- Dash in package name → underscore in imports
- __init__.py required for each importable folder
- Editable install (-e) links to source for development
- No build step needed for development!

Related: [[Python Import System for JavaScript Developers]], [[Virtual Environment Setup]], [[Python Development Workflow]]