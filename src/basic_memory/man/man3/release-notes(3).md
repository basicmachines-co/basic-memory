---
title: release-notes(3)
type: manpage
section: 3
name: release-notes
summary: return the latest product release notes
generated: hand
tool: release_notes
verified: 0.21.6 mcp
---

# release-notes(3)

## NAME

**release-notes** — return the latest product release notes

## SYNOPSIS

```
release_notes()
```

## DESCRIPTION

Returns the bundled release-notes markdown so agents can summarize what
changed without a web fetch. Static content shipped with the package — it
reflects the installed version's snapshot, not a live feed. No parameters,
read-only.

## MCP USAGE

Verified:

```
release_notes()
# → "# Release Notes ..." markdown for the installed version
```

## GOTCHAS

- [gotcha] Content is frozen at package build time — for current news check the repository releases page #freshness
- [bug] Shares the {{OSS_DISCOUNT_CODE}} placeholder bug with cloud-info — see basicmachines-co/basic-memory#958 (fixed in #971, pending release) #templating

## SEE ALSO

- see_also [[cloud-info(3)]]
