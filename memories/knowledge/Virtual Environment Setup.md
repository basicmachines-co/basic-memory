---
title: Virtual Environment Setup
type: note
permalink: knowledge/virtual-environment-setup
---

# Virtual Environment Setup

Understanding Python virtual environments and installation modes.

## Two Installation Modes

### 1. Development Mode (Editable Install)

**Location:** .venv/ in project directory
**Purpose:** Development, testing, IDE intellisense
**Command:** uv pip install -e .

**Characteristics:**
- Creates SYMLINK to source code
- Changes to src/ apply instantly
- No reinstall needed
- Visible to IDE for autocomplete and go-to-definition

**Like:** npm install with local node_modules

### 2. Production Mode (Tool Install)

**Location:** ~/.local/share/uv/tools/package-name/
**Purpose:** Run CLI globally from anywhere
**Command:** uv tool install .

**Characteristics:**
- Creates FULL COPY of code
- Isolated environment
- Need to reinstall to pick up changes
- Creates wrapper script in ~/.local/bin/

**Like:** npm install -g

## Setup Commands

**Create virtual environment:**
```bash
uv venv
```

**Activate environment:**
```bash
source .venv/bin/activate
```

**Install in editable mode:**
```bash
uv pip install -e .
```

**Install as global tool:**
```bash
uv tool install .
```

## Key Differences

| Aspect | Dev (.venv) | Tool Install |
|--------|-------------|--------------|
| Location | ./.venv/ | ~/.local/share/uv/tools/ |
| Type | Symlink to src/ | Full copy |
| Changes | Instant | Need reinstall |
| Use for | Development | Global CLI |

## IDE Configuration

After creating .venv:
1. Open Command Palette (Cmd+Shift+P)
2. Select "Python: Select Interpreter"
3. Choose ./.venv/bin/python
4. Reload window

This enables:
- Go to Definition (Cmd+Click)
- Autocomplete
- Type checking
- Import resolution

## Why Both?

- **Dev environment:** For editing and testing code
- **Tool install:** For using CLI globally in daily work
- Can update tool install after dev changes with: uv tool install --force .

Related: [[Python Package Setup with pyproject.toml]], [[Python Development Workflow]]