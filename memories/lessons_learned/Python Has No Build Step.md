---
title: Python Has No Build Step
type: note
permalink: lessons-learned/python-has-no-build-step
---

# Python Has No Build Step

Key insight: Python development is fundamentally different from TypeScript/Next.js.

## The Big Difference

**JavaScript/TypeScript:**
- TypeScript must be compiled to JavaScript
- Next.js bundles and optimizes
- Build step takes time
- Changes require rebuild

**Python:**
- Interpreted language
- Reads .py files directly at runtime
- No compilation needed
- Changes are instant

## What This Means

### For Development
- Edit code → Run immediately
- No npm run build or pnpm build
- No waiting for compilation
- Faster iteration cycle

### Editable Install is Key
```bash
uv pip install -e .
```

The -e flag creates a symlink to source code:
- Python imports follow link to actual source
- Changes to src/ apply instantly
- No reinstall needed

### When DO You Build?

Only for distribution (like publishing to npm):

```bash
uv build
```

Creates:
- .whl file (wheel - like .tgz)
- .tar.gz (source distribution)

But this is ONLY for publishing to PyPI, not for development.

## Common Misconception

Coming from JavaScript, you might think:
"Where's the build step? How do I compile?"

Answer: You don't! Python doesn't need it.

## Tool Install is Different

uv tool install DOES build:
1. Creates wheel package
2. Installs to isolated environment
3. Makes global CLI command

This is like: pnpm build + npm install -g

But for development in your project, NO build step needed.

## Practical Impact

**Development speed:**
- Faster feedback loop
- No build wait times
- Edit and test immediately

**Mental model shift:**
- Stop thinking about compilation
- Think: edit source → run
- Like running JavaScript without transpilation

Related: [[Python Development Workflow]], [[Virtual Environment Setup]]