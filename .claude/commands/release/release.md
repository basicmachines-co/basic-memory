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
   - CHANGELOG.md contains entry for target version **already landed on `main`**
     (main only accepts changes via PR, so the changelog entry must go through
     its own PR before running the release; the recipe pre-flight-checks for a
     `## vX.Y.Z` heading)
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
- ✅ Changelog entry check (must already be on `main`)
- ✅ Quality checks (`just lint` + `just typecheck`)
- ✅ Version update across all consolidated manifests via `just set-version` (Python
  package + Claude Code plugin/marketplaces + Codex plugin + Hermes + OpenClaw)
- ✅ Release PR: commits the bump on a `release/vX.Y.Z` branch, opens a PR
  (`chore(core): release vX.Y.Z`), and rebase-merges it — the `main` ruleset
  rejects direct pushes and the repo disallows merge commits
- ✅ Tags the rebased bump commit on `main` (found by commit subject, since
  the rebase rewrites the SHA) and pushes the tag
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

#### MCP Registry Publication

After PyPI release is published, update the MCP registry:

1. **Verify PyPI Release**
   - Confirm package is live: https://pypi.org/project/basic-memory/<version>/
   - The `server.json` version was auto-updated by `just release`

2. **Publish to MCP Registry**
   ```bash
   # from the basic-memory repo root
   mcp-publisher publish
   ```

   If not authenticated:
   ```bash
   mcp-publisher login github
   # Follow device authentication flow
   mcp-publisher publish
   ```

3. **Verify Publication**
   ```bash
   curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=basic-memory"
   ```

**Note:** The `mcp-publisher` CLI can be installed via Homebrew (`brew install mcp-publisher`) or from GitHub releases.

#### Website Updates

**1. basicmachines.co** (sibling `basicmachines.co` repo)
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

**2. docs.basicmemory.com** (sibling `docs.basicmemory.com` repo)
   - **Goal**: Add a What's New page for the release and bump the homepage badge
   - **Site shape**: Nuxt/Docus content site. The changelog page
     (`content/2.whats-new/*.changelog.md`) auto-fetches GitHub releases — no
     manual changelog update needed. See that repo's CLAUDE.md "Version Bump
     Checklist".
   - **What to do**:
     1. Pull latest from GitHub: `git pull origin main`
     2. Create release branch: `git checkout -b release/v{VERSION}`
     3. Read `CHANGELOG.md` in the `basic-memory` repo to get release content
     4. **New minor/major release**: add `content/2.whats-new/1.v{VERSION}.md`
        modeled on the previous version page (frontmatter title/description,
        headline feature first, then sections, then an Upgrading note) and
        renumber the existing what's-new pages down one slot (URLs don't
        change — Nuxt strips the numeric prefixes)
     5. **Patch release**: append a short note to the current version's page
        instead of creating a new one
     6. Update the homepage version badge in `content/index.md` (the
        `v0.XX →` button text and its `to: /whats-new/v{VERSION}` link)
     7. If the release adds user-facing features, update the relevant guide
        and reference pages (`content/3.cloud/`, `content/9.reference/`)
     8. Commit: `git commit -s -m "docs: add v{VERSION} release notes"`
     9. Push branch and open a PR; merge after the release is tagged
   - **Deploy**: push to main auto-deploys to development; production requires
     manual workflow dispatch via GitHub Actions

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
🔌 MCP Registry: https://registry.modelcontextprotocol.io
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
- Version is automatically updated across **all** consolidated manifests via
  `just set-version <version>` (which calls `scripts/update_versions.py`): the
  Python package (`__init__.py`, `server.json`) **and** the plugin/agent artifacts
  (Claude Code `plugin.json` + root/local marketplaces, Codex `plugin.json`,
  Hermes `plugin.yaml` + `__init__.py`, OpenClaw `package.json`). To bump only
  the plugin/agent artifacts
  out of band, use `just set-packages-version <version>` (preview with
  `just set-packages-version-dry-run <version>`).
- Triggers automated GitHub release with changelog
- Package is published to PyPI for `pip` and `uv` users
- Homebrew formula is automatically updated for stable releases
- MCP Registry is updated manually via `mcp-publisher publish`
- Supports multiple installation methods (uv, pip, Homebrew)
