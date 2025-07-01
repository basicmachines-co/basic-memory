# /release-check - Pre-flight Release Validation

Comprehensive pre-flight check for release readiness without making any changes.

## Usage
```
/release-check [version]
```

**Parameters:**
- `version` (optional): Version to validate like `v0.13.0`. If not provided, determines from context.

## Implementation

You are an expert QA engineer for the Basic Memory project. When the user runs `/release-check`, execute the following validation steps:

### Step 1: Environment Validation
1. **Git Status Check**
   - Verify working directory is clean
   - Confirm on `main` branch
   - Check if ahead/behind origin

2. **Version Validation**
   - Validate version format if provided
   - Check for existing tags with same version
   - Verify version increments properly from last release

### Step 2: Code Quality Gates
1. **Test Suite Validation**
   ```bash
   just test
   ```
   - All tests must pass
   - Check test coverage (target: 95%+)
   - Validate no skipped critical tests

2. **Code Quality Checks**
   ```bash
   just lint
   just type-check
   ```
   - No linting errors
   - No type checking errors
   - Code formatting is consistent

### Step 3: Documentation Validation
1. **Changelog Check**
   - CHANGELOG.md contains entry for target version
   - Entry includes all major features and fixes
   - Breaking changes are documented

2. **Documentation Currency**
   - README.md reflects current functionality
   - CLI reference is up to date
   - MCP tools are documented

### Step 4: Dependency Validation
1. **Security Scan**
   - No known vulnerabilities in dependencies
   - All dependencies are at appropriate versions
   - No conflicting dependency versions

2. **Build Validation**
   - Package builds successfully
   - All required files are included
   - No missing dependencies

### Step 5: Issue Tracking Validation
1. **GitHub Issues Check**
   - No critical open issues blocking release
   - All milestone issues are resolved
   - High-priority bugs are fixed

2. **Testing Coverage**
   - Integration tests pass
   - MCP tool tests pass
   - Cross-platform compatibility verified

## Report Format

Generate a comprehensive report:

```
🔍 Release Readiness Check for v0.13.0

✅ PASSED CHECKS:
├── Git status clean
├── On main branch  
├── All tests passing (744/744)
├── Test coverage: 98.2%
├── Type checking passed
├── Linting passed
├── CHANGELOG.md updated
└── No critical issues open

⚠️  WARNINGS:
├── 2 medium-priority issues still open
└── Documentation could be updated

❌ BLOCKING ISSUES:
└── None found

🎯 RELEASE READINESS: ✅ READY

Recommended next steps:
1. Address warnings if desired
2. Run `/release v0.13.0` when ready
```

## Validation Criteria

### Must Pass (Blocking)
- [ ] All tests pass
- [ ] No type errors
- [ ] No linting errors  
- [ ] Working directory clean
- [ ] On main branch
- [ ] CHANGELOG.md has version entry
- [ ] No critical open issues

### Should Pass (Warnings)
- [ ] Test coverage >95%
- [ ] No medium-priority open issues
- [ ] Documentation up to date
- [ ] No dependency vulnerabilities

## Context
- This is a read-only validation - makes no changes
- Provides confidence before running actual release
- Helps identify issues early in release process
- Can be run multiple times safely