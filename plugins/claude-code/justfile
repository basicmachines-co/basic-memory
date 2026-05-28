# Basic Memory Claude Code plugin checks

repo_root := "../.."

# Portable manifest and bundled agent/skill layout checks
manifest-check:
    python3 {{repo_root}}/scripts/validate_claude_plugin.py .

# Strict Claude Code plugin validation. Requires the `claude` CLI.
validate:
    claude plugin validate . --strict

# CI-safe check that does not require the host Claude Code CLI.
ci-check: manifest-check

# Full local check for maintainers.
check: manifest-check validate

# Show available recipes
default:
    @just --list
