# Legal File Inventory Guide

## Overview

This guide documents the comprehensive file inventory script created for Basic Memory's legal documentation needs, including copyright assignments and company agreement exhibits.

## Repository Analysis Summary

### Repository Structure
- **Primary Language**: Python (3,958 files)
- **License**: GNU Affero General Public License v3.0
- **Total Contributors**: 12 identified contributors
- **Main Contributors**:
  - `phernandez@basicmachines.co` / `paul@basicmachines.co` (Paul Hernandez) - 700+ commits
  - `groksrc@gmail.com` / `groksrc@users.noreply.github.com` (Drew Cain) - 18+ commits
  - Various AI bots and automated systems

### File Type Distribution
Based on the analysis, the repository contains:

| Extension | Count | Purpose |
|-----------|-------|---------|
| `.py` | 3,958 | Python source code |
| `.pyi` | 4,536 | Python type stubs (dependencies) |
| `.pyc` | 1,435 | Compiled Python (excluded from inventory) |
| `.md` | 34 | Documentation files |
| `.toml` | 179 | Configuration files |
| `.html` | 119 | Coverage reports and documentation |
| `.txt` | 127 | Various text files |

### Contributors by Email Domain

**Basic Machines Contributors:**
- `phernandez@basicmachines.co` - 547 commits (Primary maintainer)
- `paul@basicmachines.co` / `paulmh@gmail.com` - 170 commits (Paul Hernandez)
- `claude@basicmachines.co` - 3 commits (AI assistant)

**External Contributors:**
- `groksrc@gmail.com` / `groksrc@users.noreply.github.com` - 18 commits (Drew Cain)
- Various one-time contributors (1-2 commits each)

**Automated Systems:**
- GitHub Actions, semantic-release, and other bots

## Legal Inventory Script Features

### What It Includes

**Source Files:**
- All Python source code (`.py` files)
- Configuration files (`.toml`, `.yaml`, `.json`, etc.)
- Documentation (`.md`, `.rst`, `.txt`)
- Build and deployment scripts
- Database migrations and SQL files
- Legal and license files

**Metadata for Each File:**
- File path, size, and creation/modification dates
- Git history (creation date, last modified, commit count)
- Contributors and their line contributions
- Primary author identification
- File categorization
- SHA-256 hash for integrity verification

### What It Excludes

**Generated/Build Artifacts:**
- `__pycache__/` and `.pyc` files
- Build directories (`build/`, `dist/`, `htmlcov/`)
- Coverage reports and cache files

**Dependencies:**
- Virtual environment files (`.venv/`, `venv/`)
- Third-party packages (`site-packages/`, `*.dist-info/`)
- Lock files (`uv.lock`, `package-lock.json`)

**IDE/Editor Files:**
- `.idea/`, `.vscode/`, `.DS_Store`
- Temporary and swap files

**Version Control:**
- `.git/` directory contents
- Git configuration files

## Usage Instructions

### Basic Usage

```bash
# Generate CSV inventory (default)
python3 legal_file_inventory.py

# Generate Markdown report
python3 legal_file_inventory.py --format markdown --output legal_report.md

# Generate JSON with full metadata
python3 legal_file_inventory.py --format json --output legal_data.json

# Specify different repository path
python3 legal_file_inventory.py --repo-path /path/to/repo --output inventory.csv
```

### Command Line Options

- `--output`, `-o`: Output file path (default: `basic_memory_legal_inventory.csv`)
- `--format`, `-f`: Output format - `csv`, `json`, or `markdown` (default: `csv`)
- `--repo-path`, `-r`: Repository path (default: current directory)

### Output Formats

**CSV Format:**
- Suitable for spreadsheet applications
- Contains all metadata fields
- Contributors stored as JSON string in separate column

**JSON Format:**
- Complete structured data
- Includes summary statistics and detailed file information
- Best for programmatic processing

**Markdown Format:**
- Human-readable report
- Summary statistics and contributor rankings
- Detailed table of all files

## Legal Documentation Applications

### Copyright Assignment Use Cases

1. **Contributor Identification**: The script identifies all contributors to each file based on git blame analysis
2. **Primary Author Recognition**: Determines the primary author of each file (contributor with most lines)
3. **Contribution Metrics**: Provides line counts and commit counts per contributor
4. **File Categorization**: Groups files by purpose (source code, documentation, configuration, etc.)

### Company Agreement Exhibits

The inventory provides comprehensive documentation of:

1. **Intellectual Property Scope**: All source code files and their origins
2. **Contributor Tracking**: Complete list of all individuals who have contributed code
3. **File Integrity**: SHA-256 hashes for verification of file contents
4. **Historical Documentation**: Git creation dates and modification history

### Due Diligence Documentation

The script generates data suitable for:

1. **Legal Review**: Comprehensive file listing with contributor information
2. **IP Audit**: Identification of all copyright holders
3. **License Compliance**: Verification of file ownership and licensing
4. **Asset Documentation**: Complete inventory of company code assets

## Integration with Legal Processes

### Recommended Workflow

1. **Generate Inventory**: Run the script to create current file inventory
2. **Legal Review**: Have legal counsel review the contributor list and file categorization
3. **Copyright Assignment**: Use contributor data to ensure proper copyright assignments
4. **Document Attachment**: Include inventory as exhibit in company agreements
5. **Regular Updates**: Re-run inventory for significant releases or legal milestones

### Key Legal Considerations

**AGPL-3.0 License:**
- All files are under AGPL-3.0 unless otherwise specified
- Contributors retain copyright but license under AGPL-3.0 terms
- Company needs proper copyright assignments for proprietary licensing

**Contributor Rights:**
- External contributors may retain rights to their contributions
- Proper contributor license agreements (CLAs) should be in place
- AI-generated content may have different legal status

**File Categories for Legal Review:**
- **Source Code**: Core IP, requires copyright assignment
- **Configuration**: May contain proprietary deployment information
- **Documentation**: Usually less sensitive but may contain trade secrets
- **Legal/License**: Critical for compliance verification

## Maintenance and Updates

### When to Regenerate Inventory

- Before major releases
- During legal document preparation
- After significant contributor additions
- For annual compliance reviews
- During acquisition or investment processes

### Validation Steps

1. Verify contributor email mapping is accurate
2. Check that file categorization makes sense
3. Ensure excluded files are appropriate
4. Review contributor counts against expectations
5. Validate file hashes for integrity

## Technical Implementation Notes

### Git Integration
- Uses `git blame --line-porcelain` for detailed contributor analysis
- Tracks file history with `git log --follow`
- Handles renamed files and complex git histories

### Performance Considerations
- Scans repository incrementally to handle large codebases
- Excludes binary dependencies to reduce processing time
- Caches git operations where possible

### Error Handling
- Gracefully handles files not in git
- Continues processing if individual files fail
- Provides detailed error reporting

## Security and Privacy

### Data Sensitivity
- Contains contributor names and email addresses
- May reveal internal file structure and organization
- Should be treated as confidential legal documentation

### Recommended Handling
- Limit access to legal and executive team
- Store in secure, access-controlled systems
- Consider redacting contributor emails for external sharing
- Regular cleanup of generated inventory files

## Troubleshooting

### Common Issues

**Git Not Available:**
- Ensure git is installed and repository is initialized
- Check that the script is run from within the repository

**Permission Errors:**
- Ensure read access to all repository files
- Check write permissions for output directory

**Large Repository Performance:**
- Consider running on subsets of files for very large repositories
- Use `--repo-path` to target specific subdirectories

**Contributor Mapping Issues:**
- Git usernames may not match real identities
- Consider post-processing to normalize contributor names
- Review .mailmap files for git identity consolidation

This comprehensive legal file inventory system provides the foundation for proper intellectual property documentation and legal compliance for the Basic Memory project.