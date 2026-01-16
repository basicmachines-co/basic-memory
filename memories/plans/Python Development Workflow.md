---
title: Python Development Workflow
type: note
permalink: plans/python-development-workflow
---

# Python Development Workflow

Complete workflow for developing Python packages locally.

## First-Time Setup

**Clone and install:**
```bash
git clone https://github.com/org/project.git
cd project
uv venv
source .venv/bin/activate
uv pip install -e .
```

**Configure IDE:**
- Cmd+Shift+P → "Python: Select Interpreter"
- Choose ./.venv/bin/python
- Reload window

## Daily Development Loop

**No build step required!**

1. Activate environment
   ```bash
   source .venv/bin/activate
   ```

2. Make changes to source code
   Edit files in src/package_name/

3. Test immediately
   ```bash
   python -m package_name.cli.main ...
   # or
   pytest tests/
   ```

4. Changes are INSTANT - no rebuild needed!

## Updating Global CLI

After making changes, update global tool:

```bash
cd /path/to/project
uv tool install --force .
```

Now bm or package-name commands use your latest code.

## Installation Sources

**From PyPI (published version):**
```bash
uv tool install package-name
```

**From local source:**
```bash
uv tool install .
```

**From Git:**
```bash
uv tool install git+https://github.com/org/repo.git
```

**From wheel file:**
```bash
uv tool install ./dist/package-0.1.0-py3-none-any.whl
```

## Key Commands

| Command | Purpose |
|---------|---------|
| uv venv | Create virtual environment |
| source .venv/bin/activate | Activate environment |
| uv pip install -e . | Editable install (dev) |
| uv tool install . | Global install (production) |
| uv tool install --force . | Update global install |
| uv tool list | See installed tools |
| just test | Run tests |
| just lint | Check code style |
| just format | Format code |

## Python vs Next.js Comparison

| Task | Next.js | Python |
|------|---------|--------|
| Install deps | pnpm install | uv pip install -e . |
| Dev mode | pnpm dev | Just activate venv |
| Build | pnpm build | Not needed! |
| Run | pnpm start | python -m package |
| Hot reload | Built-in | Automatic |
| Type check | tsc --noEmit | just type-check |
| Lint | eslint | just lint |

## Key Insight

Python is interpreted - no compilation needed for development. Edit and run!

Related: [[Virtual Environment Setup]], [[Python Package Setup with pyproject.toml]]