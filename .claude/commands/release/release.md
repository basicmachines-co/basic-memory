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

#### Version Check
1. Check current version in `src/basic_memory/__init__.py`
2. Verify new version format matches `v\d+\.\d+\.\d+` pattern
3. Confirm version is higher than current version

#### Git Status
1. Check current git status for uncommitted changes
2. Verify we're on the `main` branch
3. Confirm no existing tag with this version

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
- ✅ Release workflow trigger (automatic on tag push)

The GitHub Actions workflow (`.github/workflows/release.yml`) then:
- ✅ Builds the package using `uv build`
- ✅ Creates GitHub release with auto-generated notes
- ✅ Publishes to PyPI
- ✅ Updates Homebrew formula (stable releases only)

### Step 3: Monitor Release Process
1. Verify tag push triggered the workflow (should start automatically within seconds)
2. Monitor workflow progress at: https://github.com/basicmachines-co/basic-memory/actions
3. Watch for successful completion of both jobs:
   - `release` - Builds package and publishes to PyPI
   - `homebrew` - Updates Homebrew formula (stable releases only)
4. Check for any workflow failures and investigate logs if needed

### Step 4: Post-Release Validation

#### GitHub Release
1. Verify GitHub release is created at: https://github.com/basicmachines-co/basic-memory/releases/tag/<version>
2. Check that release notes are auto-generated from commits
3. Validate release assets (`.whl` and `.tar.gz` files are attached)

#### PyPI Publication
1. Verify package published at: https://pypi.org/project/basic-memory/<version>/
2. Test installation: `uv tool install basic-memory`
3. Verify installed version: `basic-memory --version`

#### Homebrew Formula (Stable Releases Only)
1. Check formula update at: https://github.com/basicmachines-co/homebrew-basic-memory
2. Verify formula version matches release
3. Test Homebrew installation: `brew install basicmachines-co/basic-memory/basic-memory`

#### Website Updates

**1. basicmachines.co** (`/Users/drew/code/basicmachines.co`)
   - **Goal**: Update version number displayed on the homepage
   - **Location**: Search for "Basic Memory v0." in the codebase to find version displays
   - **What to update**:
     - Hero section heading that shows "Basic Memory v{VERSION}"
     - "What's New in v{VERSION}" section heading
     - Feature highlights array (look for array of features with title/description)
   - **Process**:
     1. Pull latest from GitHub: `git pull origin main`
     2. Create release branch: `git checkout -b release/v{VERSION}`
     3. Search codebase for current version number (e.g., "v0.16.1")
     4. Update version numbers to new release version
     5. Update feature highlights with 3-5 key features from this release (extract from CHANGELOG.md)
     6. Commit changes: `git commit -m "chore: update to v{VERSION}"`
     7. Push branch: `git push origin release/v{VERSION}`
   - **Deploy**: Follow deployment process for basicmachines.co

**2. docs.basicmemory.com** (`/Users/drew/code/docs.basicmemory.com`)
   - **Goal**: Add new release notes section to the latest-releases page
   - **File**: `src/pages/latest-releases.mdx`
   - **What to do**:
     1. Pull latest from GitHub: `git pull origin main`
     2. Create release branch: `git checkout -b release/v{VERSION}`
     3. Read the existing file to understand the format and structure
     4. Read `/Users/drew/code/basic-memory/CHANGELOG.md` to get release content
     5. Add new release section **at the top** (after MDX imports, before other releases)
     6. Follow the existing pattern:
        - Heading: `## [v{VERSION}](github-link) — YYYY-MM-DD`
        - Focus statement if applicable
        - `<Info>` block with highlights (3-5 key items)
        - Sections for Features, Bug Fixes, Breaking Changes, etc.
        - Link to full changelog at the end
        - Separator `---` between releases
     7. Commit changes: `git commit -m "docs: add v{VERSION} release notes"`
     8. Push branch: `git push origin release/v{VERSION}`
   - **Source content**: Extract and format sections from CHANGELOG.md for this version
   - **Deploy**: Follow deployment process for docs.basicmemory.com

**4. Announce Release**
   - Post to Discord community if significant changes
   - Update social media if major release
   - Notify users via appropriate channels

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
🍺 Homebrew: https://github.com/basicmachines-co/homebrew-basic-memory
🚀 GitHub Actions: Completed

Install with pip/uv:
  uv tool install basic-memory

Install with Homebrew:
  brew install basicmachines-co/basic-memory/basic-memory

Users can now upgrade:
  uv tool upgrade basic-memory
  brew upgrade basic-memory
```

## Context
- This creates production releases used by end users
- Must pass all quality gates before proceeding
- Uses the automated justfile target for consistency
- Version is automatically updated in `__init__.py`
- Triggers automated GitHub release with changelog
- Package is published to PyPI for `pip` and `uv` users
- Homebrew formula is automatically updated for stable releases
- Supports multiple installation methods (uv, pip, Homebrew)