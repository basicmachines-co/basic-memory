---
title: okf-check(1)
type: manpage
section: 1
name: okf-check
summary: check OKF v0.2 structural conformance without a project database
generated: cli
---

# okf-check(1)

## NAME

**okf-check** — check OKF v0.2 structural conformance without a project database

## SYNOPSIS

```
bm okf check BUNDLE_PATH [--json]
```

## DESCRIPTION

Walk a directory bundle without Basic Memory configuration, indexing, or ignore
rules. Every non-reserved .md file must be UTF-8, have parseable YAML mapping
frontmatter, and carry a non-empty string `type`. Only root index.md may have
frontmatter, containing only `okf_version`. Index sections have headings and
entries use standard Markdown links. log.md has no frontmatter and groups
recorded entries under `## YYYY-MM-DD` headings, newest first.

Diagnostics identify the file, rule, and problem. Exit status is 0 for a valid
bundle and 1 for violations or unreadable files. JSON contains `concepts` and
`diagnostics`; reserved files and assets are not counted as concepts.

Unknown types, unknown keys, missing optional fields, broken cross-links,
missing indexes, and non-Markdown assets are accepted. Version declarations
are advisory. Symlinks are diagnosed as non-portable. This checks the structural
contract, not trust, attestation execution, or every optional field convention.

The contract follows OKF v0.2 §11:
https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/e6d34fd29c1c6c75ec23078e7a8191a9c8209620/okf/SPEC.md

## OPTIONS

- **--json** — Output machine-readable diagnostics

## EXAMPLES

```
bm okf check ~/exports/research
bm okf check ~/exports/research --json
```

## SEE ALSO

- see_also [[okf-export(1)]]
