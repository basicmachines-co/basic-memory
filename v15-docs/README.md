# v0.15.0 Documentation Notes

This directory contains user-focused documentation notes for v0.15.0 changes. These notes are written from the user's perspective and will be used to update the main documentation site (docs.basicmemory.com).

## Purpose

- Capture complete user-facing details of code changes
- Provide examples and migration guidance
- Serve as source material for final documentation
- **Temporary workspace** - will be removed after release docs are complete

## Notes Structure

Each note covers a specific change or feature:
- **What changed** - User-visible behavior changes
- **Why it matters** - Impact and benefits
- **How to use** - Examples and usage patterns
- **Migration** - Steps to adapt (if breaking change)

## Coverage

Based on v0.15.0-RELEASE-DOCS.md:

### Breaking Changes
- [x] explicit-project-parameter.md (SPEC-6: #298)
- [x] default-project-mode.md

### Configuration
- [x] project-root-env-var.md (#334)
- [x] basic-memory-home.md (clarify relationship with PROJECT_ROOT)
- [x] env-var-overrides.md

### Cloud Features
- [x] cloud-authentication.md (SPEC-13: #327)
- [x] cloud-bisync.md (SPEC-9: #322)
- [x] cloud-mount.md (#306)
- [x] cloud-mode-usage.md

### Security & Performance
- [x] env-file-removal.md (#330)
- [x] gitignore-integration.md (#314)
- [x] sqlite-performance.md (#316)
- [x] background-relations.md (#319)
- [x] api-performance.md (SPEC-11: #315)

### Bug Fixes & Platform
- [x] bug-fixes.md (13+ fixes including #328, #329, #287, #281, #330, Python 3.13)

### Integrations
- [x] chatgpt-integration.md (ChatGPT MCP tools, remote only, Pro subscription required)

### AI Assistant Guides
- [x] ai-assistant-guide-extended.md (Extended guide for docs site with comprehensive examples)

## Usage

From docs.basicmemory.com repo, reference these notes to create/update:
- Migration guides
- Feature documentation
- Release notes
- Getting started guides
