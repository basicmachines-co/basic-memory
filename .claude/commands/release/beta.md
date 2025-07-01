# /beta - Create Beta Release

Create a new beta release using the automated justfile target with quality checks and tagging.

## Usage
```
/beta <version>
```

**Parameters:**
- `version` (required): Beta version like `v0.13.2b1` or `v0.13.2rc1`

## Implementation

You are an expert release manager for the Basic Memory project. When the user runs `/beta`, execute the following steps:

### Step 1: Pre-flight Validation
1. Verify version format matches `v\d+\.\d+\.\d+(b\d+|rc\d+)` pattern
2. Check current git status for uncommitted changes
3. Verify we're on the `main` branch
4. Confirm no existing tag with this version

### Step 2: Use Justfile Automation
Execute the automated beta release process:
```bash
just beta <version>
```

The justfile target handles:
- ✅ Beta version format validation (supports b1, b2, rc1, etc.)
- ✅ Git status and branch checks
- ✅ Quality checks (`just check` - lint, format, type-check, tests)
- ✅ Version update in `src/basic_memory/__init__.py`
- ✅ Automatic commit with proper message
- ✅ Tag creation and pushing to GitHub
- ✅ Beta release workflow trigger

### Step 3: Monitor Beta Release
1. Check GitHub Actions workflow starts successfully
2. Monitor workflow at: https://github.com/basicmachines-co/basic-memory/actions
3. Verify PyPI pre-release publication
4. Test beta installation: `uv tool install basic-memory --pre`

### Step 4: Beta Testing Instructions
Provide users with beta testing instructions:

```bash
# Install/upgrade to beta
uv tool install basic-memory --pre

# Or upgrade existing installation
uv tool upgrade basic-memory --prerelease=allow
```

## Version Guidelines
- **First beta**: `v0.13.2b1` 
- **Subsequent betas**: `v0.13.2b2`, `v0.13.2b3`, etc.
- **Release candidates**: `v0.13.2rc1`, `v0.13.2rc2`, etc.
- **Final release**: `v0.13.2` (use `/release` command)

## Error Handling
- If `just beta` fails, examine the error output for specific issues
- If quality checks fail, fix issues and retry
- If version format is invalid, correct and retry
- If tag already exists, increment version number

## Success Output
```
✅ Beta Release v0.13.2b1 Created Successfully!

🏷️  Tag: v0.13.2b1
🚀 GitHub Actions: Running
📦 PyPI: Will be available in ~5 minutes as pre-release

Install/test with:
uv tool install basic-memory --pre

Monitor release: https://github.com/basicmachines-co/basic-memory/actions
```

## Beta Testing Workflow
1. **Create beta**: Use `/beta v0.13.2b1`
2. **Test features**: Install and validate new functionality
3. **Fix issues**: Address bugs found during testing
4. **Iterate**: Create `v0.13.2b2` if needed
5. **Release candidate**: Create `v0.13.2rc1` when stable
6. **Final release**: Use `/release v0.13.2` when ready

## Context
- Beta releases are pre-releases for testing new features
- Automatically published to PyPI with pre-release flag
- Uses the automated justfile target for consistency
- Version is automatically updated in `__init__.py`
- Ideal for validating changes before stable release
- Supports both beta (b1, b2) and release candidate (rc1, rc2) versions