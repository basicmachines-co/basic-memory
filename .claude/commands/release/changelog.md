# /changelog - Generate or Update Changelog Entry

Analyze commits and generate formatted changelog entry for a version.

## Usage
```
/changelog <version> [type]
```

**Parameters:**
- `version` (required): Version like `v0.14.0` or `v0.14.0b1`
- `type` (optional): `beta`, `rc`, or `stable` (default: `stable`)

## Implementation

You are an expert technical writer for the Basic Memory project. When the user runs `/changelog`, execute the following steps:

### Step 1: Version Analysis
1. **Determine Commit Range**
   ```bash
   # Find last release tag
   git tag -l "v*" --sort=-version:refname | grep -v "b\|rc" | head -1
   
   # Get commits since last release
   git log --oneline ${last_tag}..HEAD
   ```

2. **Parse Conventional Commits**
   - Extract feat: (features)
   - Extract fix: (bug fixes)  
   - Extract BREAKING CHANGE: (breaking changes)
   - Extract chore:, docs:, test: (other improvements)

### Step 2: Categorize Changes
1. **Features (feat:)**
   - New MCP tools
   - New CLI commands
   - New API endpoints
   - Major functionality additions

2. **Bug Fixes (fix:)**
   - User-facing bug fixes
   - Critical issues resolved
   - Performance improvements
   - Security fixes

3. **Technical Improvements**
   - Test coverage improvements
   - Code quality enhancements
   - Dependency updates
   - Documentation updates

4. **Breaking Changes**
   - API changes
   - Configuration changes
   - Behavior changes
   - Migration requirements

### Step 3: Generate Changelog Entry
Create formatted entry following existing CHANGELOG.md style:

Example:
```markdown
## <version> (<date>)

### Features

- **Multi-Project Management System** - Switch between projects instantly during conversations
  ([`993e88a`](https://github.com/basicmachines-co/basic-memory/commit/993e88a)) 
  - Instant project switching with session context
  - Project-specific operations and isolation
  - Project discovery and management tools

- **Advanced Note Editing** - Incremental editing with append, prepend, find/replace, and section operations
  ([`6fc3904`](https://github.com/basicmachines-co/basic-memory/commit/6fc3904))
  - `edit_note` tool with multiple operation types
  - Smart frontmatter-aware editing
  - Validation and error handling

### Bug Fixes

- **#118**: Fix YAML tag formatting to follow standard specification
  ([`2dc7e27`](https://github.com/basicmachines-co/basic-memory/commit/2dc7e27))

- **#110**: Make --project flag work consistently across CLI commands
  ([`02dd91a`](https://github.com/basicmachines-co/basic-memory/commit/02dd91a))

### Technical Improvements

- **Comprehensive Testing** - 100% test coverage with integration testing
  ([`468a22f`](https://github.com/basicmachines-co/basic-memory/commit/468a22f))
  - MCP integration test suite
  - End-to-end testing framework
  - Performance and edge case validation

### Breaking Changes

- **Database Migration**: Automatic migration from per-project to unified database. 
    Data will be re-index from the filesystem, resulting in no data loss. 
- **Configuration Changes**: Projects now synced between config.json and database
- **Full Backward Compatibility**: All existing setups continue to work seamlessly
```

### Step 4: Integration
1. **Update CHANGELOG.md**
   - Insert new entry at top
   - Maintain consistent formatting
   - Include commit links and issue references

2. **Validation**
   - Check all major changes are captured
   - Verify commit links work
   - Ensure issue numbers are correct

## Smart Analysis Features

### Automatic Classification
- Detect feature additions from file changes
- Identify bug fixes from commit messages
- Find breaking changes from code analysis
- Extract issue numbers from commit messages

### Content Enhancement
- Add context for technical changes
- Include migration guidance for breaking changes
- Suggest installation/upgrade instructions
- Link to relevant documentation

## Output Format

### For Beta Releases

Example: 
```markdown
## v0.13.0b4 (2025-06-03)

### Beta Changes Since v0.13.0b3

- Fix FastMCP API compatibility issues
- Update dependencies to latest versions  
- Resolve setuptools import error

### Installation
```bash
uv tool install basic-memory --prerelease=allow
```

### Known Issues
- [List any known issues for beta testing]
```

### For Stable Releases
Full changelog with complete feature list, organized by impact and category.

## Context
- Follows existing CHANGELOG.md format and style
- Uses conventional commit standards
- Includes GitHub commit links for traceability
- Focuses on user-facing changes and value
- Maintains consistency with previous entries