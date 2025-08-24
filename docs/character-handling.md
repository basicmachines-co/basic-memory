# Character Handling and Conflict Resolution

Basic Memory handles various character encoding scenarios and file naming conventions to provide consistent permalink generation and conflict resolution. This document explains how the system works and how to resolve common character-related issues.

## Overview

Basic Memory uses a sophisticated system to generate permalinks from file paths while maintaining consistency across different operating systems and character encodings. The system normalizes file paths and generates unique permalinks to prevent conflicts.

## Character Normalization Rules

### 1. Permalink Generation

When Basic Memory processes a file path, it applies these normalization rules:

```
Original: "Finance/My Investment Strategy.md"
Permalink: "finance/my-investment-strategy"
```

**Transformation process:**
1. Remove file extension (`.md`)
2. Convert to lowercase (case-insensitive)
3. Replace spaces with hyphens
4. Replace underscores with hyphens
5. Handle international characters (transliteration for Latin, preservation for non-Latin)
6. Convert camelCase to kebab-case

### 2. International Character Support

**Latin characters with diacritics** are transliterated:
- `ø` → `o` (Søren → soren)
- `ü` → `u` (Müller → muller)
- `é` → `e` (Café → cafe)
- `ñ` → `n` (Niño → nino)

**Non-Latin characters** are preserved:
- Chinese: `中文/测试文档.md` → `中文/测试文档`
- Japanese: `日本語/文書.md` → `日本語/文書`

## Common Conflict Scenarios

### 1. Hyphen vs Space Conflicts

**Problem:** Files with existing hyphens conflict with generated permalinks from spaces.

**Example:**
```
File 1: "basic memory bug.md"     → permalink: "basic-memory-bug"
File 2: "basic-memory-bug.md"    → permalink: "basic-memory-bug" (CONFLICT!)
```

**Resolution:** The system automatically resolves this by adding suffixes:
```
File 1: "basic memory bug.md"     → permalink: "basic-memory-bug"
File 2: "basic-memory-bug.md"    → permalink: "basic-memory-bug-1"
```

**Best Practice:** Choose consistent naming conventions within your project.

### 2. Case Sensitivity Conflicts

**Problem:** Different case variations that normalize to the same permalink.

**Example on macOS:**
```
Directory: Finance/investment.md
Directory: finance/investment.md  (different on filesystem, same permalink)
```

**Resolution:** Basic Memory detects case conflicts and prevents them during sync operations with helpful error messages.

**Best Practice:** Use consistent casing for directory and file names.

### 3. Character Encoding Conflicts

**Problem:** Different Unicode normalizations of the same logical character.

**Example:**
```
File 1: "café.md" (é as single character)
File 2: "café.md" (e + combining accent)
```

**Resolution:** Basic Memory normalizes Unicode characters using NFD normalization to detect these conflicts.

### 4. Forward Slash Conflicts

**Problem:** Forward slashes in frontmatter or file names interpreted as path separators.

**Example:**
```yaml
---
permalink: finance/investment/strategy
---
```

**Resolution:** Basic Memory validates frontmatter permalinks and warns about path separator conflicts.

## Error Messages and Troubleshooting

### "UNIQUE constraint failed: entity.file_path, entity.project_id"

**Cause:** Two entities trying to use the same file path within a project.

**Common scenarios:**
1. File move operation where destination is already occupied
2. Case sensitivity differences on macOS
3. Character encoding conflicts
4. Concurrent file operations

**Resolution steps:**
1. Check for duplicate file names with different cases
2. Look for files with similar names but different character encodings
3. Rename conflicting files to have unique names
4. Run sync again after resolving conflicts

### "File path conflict detected during move"

**Cause:** Enhanced conflict detection preventing potential database integrity violations.

**What this means:** The system detected that moving a file would create a conflict before attempting the database operation.

**Resolution:** Follow the specific guidance in the error message, which will indicate the type of conflict detected.

## Best Practices

### 1. File Naming Conventions

**Recommended patterns:**
- Use consistent casing (prefer lowercase)
- Use hyphens instead of spaces for multi-word files
- Avoid special characters that could conflict with path separators
- Be consistent with directory structure casing

**Examples:**
```
✅ Good:
- finance/investment-strategy.md
- projects/basic-memory-features.md
- docs/api-reference.md

❌ Problematic:
- Finance/Investment Strategy.md  (mixed case, spaces)
- finance/Investment Strategy.md  (inconsistent case)
- docs/API/Reference.md          (mixed case directories)
```

### 2. Permalink Management

**Custom permalinks in frontmatter:**
```yaml
---
type: knowledge
permalink: custom-permalink-name
---
```

**Guidelines:**
- Use lowercase permalinks
- Use hyphens for word separation
- Avoid path separators unless creating sub-paths
- Ensure uniqueness within your project

### 3. Directory Structure

**Consistent casing:**
```
✅ Good:
finance/
  investment-strategies.md
  portfolio-management.md

❌ Problematic:  
Finance/           (capital F)
  investment-strategies.md
finance/           (lowercase f) 
  portfolio-management.md
```

## Migration and Cleanup

### Identifying Conflicts

Use Basic Memory's built-in conflict detection:

```bash
# Sync will report conflicts
basic-memory sync

# Check sync status for warnings
basic-memory status
```

### Resolving Existing Conflicts

1. **Identify conflicting files** from sync error messages
2. **Choose consistent naming convention** for your project
3. **Rename files** to follow the convention
4. **Re-run sync** to verify resolution

### Bulk Renaming Strategy

For projects with many conflicts:

1. **Backup your project** before making changes
2. **Standardize on lowercase** file and directory names
3. **Replace spaces with hyphens** in file names
4. **Use consistent character encoding** (UTF-8)
5. **Test sync after each batch** of changes

## System Enhancements

### Recent Improvements (v0.13+)

1. **Enhanced conflict detection** before database operations
2. **Improved error messages** with specific resolution guidance
3. **Character normalization utilities** for consistent handling
4. **File swap detection** for complex move scenarios
5. **Proactive conflict warnings** during permalink resolution

### Monitoring and Logging

The system now provides detailed logging for conflict resolution:

```
DEBUG: Detected potential file path conflicts for 'Finance/Investment.md': ['finance/investment.md']
WARNING: File path conflict detected during move: entity_id=123 trying to move from 'old.md' to 'new.md'
```

These logs help identify and resolve conflicts before they cause sync failures.

## Support and Resources

If you encounter character-related conflicts not covered in this guide:

1. **Check the logs** for specific conflict details
2. **Review error messages** for resolution guidance  
3. **Report issues** with examples of the conflicting files
4. **Consider the file naming best practices** outlined above

The Basic Memory system is designed to handle most character conflicts automatically while providing clear guidance for manual resolution when needed.