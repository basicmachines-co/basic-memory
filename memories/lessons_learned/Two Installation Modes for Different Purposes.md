---
title: Two Installation Modes for Different Purposes
type: note
permalink: lessons-learned/two-installation-modes-for-different-purposes
---

# Two Installation Modes for Different Purposes

Understanding why you need both editable install AND tool install.

## The Confusion

"I have uv tool install working, why do I need .venv too?"

Both serve different purposes and should coexist.

## Mode 1: Editable Install (.venv)

**Purpose:** Active development

**Setup:**
```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

**Characteristics:**
- SYMLINK to source code
- Changes apply instantly
- No reinstall needed
- IDE can see it
- Only works when venv activated

**Use for:**
- Editing code
- Testing changes
- Running tests
- IDE intellisense

**Like:** Local node_modules/ in your project

## Mode 2: Tool Install (~/.local/)

**Purpose:** Daily CLI usage

**Setup:**
```bash
uv tool install .
```

**Characteristics:**
- FULL COPY of code
- Isolated environment
- Available globally
- Need to reinstall after changes
- Always in PATH

**Use for:**
- Running CLI from any directory
- Production-like usage
- Not for development

**Like:** npm install -g

## Why Both?

**Scenario:** You're developing basic-memory

**Dev workflow:**
1. Edit src/basic_memory/utils.py in venv
2. Test immediately: pytest tests/
3. Changes work instantly!

**Daily usage:**
1. Open terminal anywhere
2. Run: bm write-note ...
3. Uses stable global install

**Update global after dev:**
```bash
uv tool install --force .
```

## Key Insight

This is NOT redundancy - it's separation of concerns:
- .venv = development sandbox
- tool install = stable production-like environment

## Comparison Table

| Aspect | .venv (Dev) | Tool Install (Prod) |
|--------|-------------|---------------------|
| Changes | Instant | Need reinstall |
| Activation | source .venv/bin/activate | Always available |
| IDE support | Yes | No |
| Use case | Development | Daily usage |
| Location | ./venv/ | ~/.local/share/uv/tools/ |

## Update Strategy

**During development:**
- Work in .venv with instant changes
- Test thoroughly

**When satisfied:**
- Update global: uv tool install --force .
- Now daily usage has your improvements

## Mental Model

Think of it like:
- .venv = Your workshop (mess, experimentation, tools)
- tool install = Production deployment (clean, stable, reliable)

Related: [[Virtual Environment Setup]], [[Python Development Workflow]]