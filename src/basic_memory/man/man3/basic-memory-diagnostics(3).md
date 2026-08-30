---
title: basic-memory-diagnostics(3)
type: manpage
section: 3
name: basic-memory-diagnostics
summary: report version, system, and redacted configuration for troubleshooting
generated: registry
tool: basic_memory_diagnostics
verified: 0.23.2 local
---

# basic-memory-diagnostics(3)

## NAME

**basic-memory-diagnostics** — report version, system, and redacted configuration for troubleshooting

## SYNOPSIS

```
basic_memory_diagnostics()
```

## DESCRIPTION

Returns a markdown report for support requests and install debugging: the
basic-memory package version and API version, the Python version, platform,
and architecture, and the config file path with its contents as a JSON block.
Secrets and API keys are redacted before anything is emitted.

Read-only by contract: the tool only computes the config path — it never
creates the data directory or touches the database, so it works on a broken
or half-installed setup, which is exactly when it is needed.

## MCP USAGE

Verified locally (0.23.2):

```
basic_memory_diagnostics()
# → "# Basic Memory Diagnostics
#
#    ## Version
#    - basic-memory: 0.23.2
#    - API: v2
#
#    ## System
#    - Python: 3.14.5 (...)
#    - Platform: macOS-26.6-arm64-arm-64bit-Mach-O
#    - Architecture: arm64
#
#    ## Configuration
#    - Config path: ~/.basic-memory/config.json
#    - Config exists: True
#    ```json
#    { redacted config ... }
#    ```"
```

## GOTCHAS

- [gotcha] The config JSON block lists every configured project with its path and mode — redaction removes secrets, not project names, so treat the report as private when sharing #privacy
- [gotcha] A missing or unreadable config file is reported inline (`<config file not found>` / `<error reading config: ...>`) instead of failing — no result is still a diagnostic #resilience

## SEE ALSO

- see_also [[list-memory-projects(3)]]
- see_also [[cloud-info(3)]]
