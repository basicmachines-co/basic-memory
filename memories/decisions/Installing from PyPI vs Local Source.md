---
title: Installing from PyPI vs Local Source
type: note
permalink: decisions/installing-from-py-pi-vs-local-source
---

# Installing from PyPI vs Local Source

Decision guide for choosing installation source.

## The Choice

When installing a Python package, you can install from multiple sources.

## Option 1: PyPI (Package Registry)

**Command:**
```bash
uv tool install package-name
uv tool install package-name==0.14.3
```

**When to use:**
- Using stable, published version
- Don't need local modifications
- Want automatic updates
- Standard usage

**Pros:**
- Always published, tested versions
- Easy to upgrade
- No local source needed

**Cons:**
- Can't modify code
- Must wait for releases
- Can't test unreleased features

**Like:** npm install -g package

## Option 2: Local Source

**Command:**
```bash
uv tool install .
uv tool install /path/to/project
```

**When to use:**
- Forked the project
- Made local modifications
- Testing unreleased changes
- Contributing to project

**Pros:**
- Use YOUR modified version
- Test changes immediately
- Can customize

**Cons:**
- Need local source code
- Manual updates
- Must reinstall after changes

**Like:** npm install -g .

## Option 3: Git Repository

**Command:**
```bash
uv tool install git+https://github.com/org/repo.git
uv tool install git+https://github.com/org/repo.git@branch
```

**When to use:**
- Want latest development version
- Specific branch/commit needed
- No local modifications

**Pros:**
- Always latest code
- No local clone needed
- Can target specific commits

**Cons:**
- Might be unstable
- Harder to modify
- Network dependency

## How UV Knows

The argument determines source:
- package-name → PyPI
- . or /path → Local
- git+https:// → Git

## Verification

Check installation receipt:
```bash
cat ~/.local/share/uv/tools/package-name/uv-receipt.toml
```

**From PyPI:**
```toml
requirements = [{ name = "basic-memory", version = "0.14.3" }]
```

**From local:**
```toml
requirements = [{ name = "basic-memory", directory = "/path/to/source" }]
```

## Our Decision (basic-memory)

**Use local source because:**
1. We have local modifications (config.py, utils.py)
2. Want to test changes before publishing
3. Developing features not yet in PyPI
4. Have access to source code

**Command used:**
```bash
cd /Users/tyler/code/mcp/basic-memory
uv tool install .
```

## Update Strategy

**For development:**
1. Edit in .venv (instant changes)
2. Test thoroughly
3. Update tool: uv tool install --force .

**For production users:**
1. Install from PyPI: uv tool install basic-memory
2. Get stable, tested releases
3. Upgrade: uv tool install --upgrade basic-memory

## Key Insight

Local install is for DEVELOPMENT.
PyPI install is for USAGE.

Related: [[Python Development Workflow]], [[Two Installation Modes for Different Purposes]]