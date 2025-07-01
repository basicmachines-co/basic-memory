# /release - Create Stable Release

Create a stable release using the automated justfile target with comprehensive validation.

## Usage
```
/release <version>
```

**Parameters:**
- `version` (required): Release version like `v0.13.2`

## Implementation

You are an expert release manager for the Basic Memory project. When the user runs `/release`, execute the following steps:

### Step 1: Pre-flight Validation
1. Verify version format matches `v\d+\.\d+\.\d+` pattern
2. Check current git status for uncommitted changes  
3. Verify we're on the `main` branch
4. Confirm no existing tag with this version

#### Documentation Validation
1. **Changelog Check**
   - CHANGELOG.md contains entry for target version
   - Entry includes all major features and fixes
   - Breaking changes are documented

### Step 2: Use Justfile Automation
Execute the automated release process:
```bash
just release <version>
```

The justfile target handles:
- ✅ Version format validation
- ✅ Git status and branch checks
- ✅ Quality checks (`just check` - lint, format, type-check, tests)
- ✅ Version update in `src/basic_memory/__init__.py`
- ✅ Automatic commit with proper message
- ✅ Tag creation and pushing to GitHub
- ✅ Release workflow trigger

### Step 3: Monitor Release Process
1. Check that GitHub Actions workflow starts successfully
2. Monitor workflow completion at: https://github.com/basicmachines-co/basic-memory/actions
3. Verify PyPI publication
4. Test installation: `uv tool install basic-memory`

### Step 4: Post-Release Validation
1. Verify GitHub release is created automatically
2. Check PyPI publication
3. Validate release assets
4. Update any post-release documentation

## Pre-conditions Check
Before starting, verify:
- [ ] All beta testing is complete
- [ ] Critical bugs are fixed
- [ ] Breaking changes are documented
- [ ] CHANGELOG.md is updated (if needed)
- [ ] Version number follows semantic versioning

## Error Handling
- If `just release` fails, examine the error output for specific issues
- If quality checks fail, fix issues and retry
- If changelog entry missing, update CHANGELOG.md and commit before retrying
- If GitHub Actions fail, check workflow logs for debugging

## Success Output
```
🎉 Stable Release v0.13.2 Created Successfully!

🏷️  Tag: v0.13.2
📋 GitHub Release: https://github.com/basicmachines-co/basic-memory/releases/tag/v0.13.2
📦 PyPI: https://pypi.org/project/basic-memory/0.13.2/
🚀 GitHub Actions: Completed

Install with:
uv tool install basic-memory

Users can now upgrade:
uv tool upgrade basic-memory
```

## Context
- This creates production releases used by end users
- Must pass all quality gates before proceeding
- Uses the automated justfile target for consistency
- Version is automatically updated in `__init__.py`
- Triggers automated GitHub release with changelog
- Leverages uv-dynamic-versioning for package version management