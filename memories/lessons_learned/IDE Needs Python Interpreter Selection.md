---
title: IDE Needs Python Interpreter Selection
type: note
permalink: lessons-learned/ide-needs-python-interpreter-selection
---

# IDE Needs Python Interpreter Selection

Key lesson: Unlike JavaScript/Node.js, Python IDEs require explicit interpreter selection.

## The Problem

Cmd+Click on imports doesn't work by default in Python projects.

## Why It's Different

**JavaScript/Node.js:**
- IDE auto-detects node_modules/
- Imports resolve automatically
- Works out of the box

**Python:**
- Multiple Python installations possible (system, venv, conda, tools)
- IDE doesn't know which to use
- Must explicitly select interpreter

## The Solution

### Step 1: Create Virtual Environment
```bash
uv venv
uv pip install -e .
```

### Step 2: Select Interpreter in IDE
- Cmd+Shift+P → "Python: Select Interpreter"
- Choose ./.venv/bin/python
- Or click Python version in bottom-right corner

### Step 3: Reload
- Cmd+Shift+P → "Developer: Reload Window"
- Or restart IDE

## What This Enables

After selection:
- Cmd+Click jumps to definitions ✓
- Autocomplete works ✓
- Type checking works ✓
- Import resolution works ✓
- Hover shows signatures ✓

## Why Install Package First?

Language server can't resolve imports for uninstalled packages.

**Wrong order:**
1. Open project
2. Try Cmd+Click
3. Doesn't work!

**Right order:**
1. Create .venv
2. Install package: uv pip install -e .
3. Select interpreter
4. Cmd+Click works!

## Common Issue

"I have the package installed globally (uv tool install), why doesn't IDE see it?"

Because:
- Tool install is in ~/.local/share/uv/tools/
- IDE doesn't look there
- Need project-local .venv/ for IDE support

## Two Installs Serve Different Purposes

**Global tool install:**
- Run CLI from anywhere
- Not for IDE

**Local .venv install:**
- IDE support
- Development
- Testing

Both can coexist!

## Verification

Check bottom-right corner of IDE shows:
```
Python 3.x.x ('.venv': venv)
```

If not, interpreter selection didn't work.

Related: [[Virtual Environment Setup]], [[Python Development Workflow]]