---
title: Python Import System for JavaScript Developers
type: note
permalink: knowledge/python-import-system-for-java-script-developers
---

# Python Import System for JavaScript Developers

A comprehensive guide to understanding Python's import system from a JavaScript perspective.

## Key Concepts

### Package Structure
- **`__init__.py`** files are like `index.js` in Node.js - they make folders importable
- Any folder with `__init__.py` becomes an importable package/module
- The folder name becomes the import name

### Import Syntax Comparison

**JavaScript:**
```javascript
import { writeNote, readNote } from './module.js';
import { writeNote as write } from './module.js';  // rename
```

**Python:**
```python
from basic_memory.mcp.tools import write_note, read_note
from basic_memory.mcp.tools import write_note as mcp_write_note  # rename
```

## Import Resolution

When you write: `from basic_memory.mcp.tools import write_note`

Python looks for:
1. Installed package "basic_memory"
2. Subfolder "mcp" with `__init__.py`
3. Subfolder "tools" with `__init__.py`
4. Check `tools/__init__.py` for "write_note" export
5. Load from `tools/write_note.py`

## Key Differences from JavaScript

- **No file extensions** in imports (`.py` is implicit)
- **No default exports** - everything is a named export
- **Dot notation** instead of file paths: `package.subpackage.module`
- **__all__** list defines public exports (like named exports)

## Navigation in IDE

- **Cmd + Click** (Mac) or **F12** - Go to Definition
- **Shift + F12** - Find All References
- **Cmd + P** - Quick file open
- **Cmd + Shift + F** - Search all files

Related: [[Python Package Setup]], [[Virtual Environment Setup]]