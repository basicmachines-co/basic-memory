---
title: okf-export(1)
type: manpage
section: 1
name: okf-export
summary: export a local project as an OKF v0.2-compatible bundle
generated: cli
---

# okf-export(1)

## NAME

**okf-export** — export a local project as an OKF v0.2-compatible bundle

## SYNOPSIS

```
bm okf export DESTINATION [--project PROJECT] [--replace] [--json]
```

## DESCRIPTION

Export a configured local project to a static directory outside the project.
Source files remain unchanged. Cloud projects must first be pulled locally.
The export follows Basic Memory's project ignore rules; non-Markdown assets
such as PDFs retain their relative paths. Symlinks are not exported.

Concept frontmatter is preserved, with absent `type` defaulting to `note`
and absent `tags` to an empty list. Wikilinks become standard Markdown links.
Exact file paths, titles, and permalinks resolve within the exported snapshot;
unresolved links remain broken links. Ambiguous aliases are not guessed.
Code examples retain literal wikilinks.

Generated index.md files contain standard links and only the root carries
`okf_version: "0.2"` frontmatter. The root log.md has no frontmatter and
records accepted Basic Memory journal history under ISO date headings.
File materialization may lag recorded acceptance; the log does not claim
every recorded version is represented by the exported files.
It does not reconstruct offline edits. Live Wiki bytes are not copied.
Unmarked files at reserved filenames must be renamed before export, even without
frontmatter; only recognized Wiki artifacts or marked OKF indexes are replaced.
Databases predating the accepted-change journal produce an empty history without
being migrated by export.

The destination is staged and checked before publication. Existing destinations
are refused unless `--replace` is explicit. A failed publication restores the
previous bundle; if restoration also fails, its bytes remain in a sibling
`.NAME.bm-okf-backup-*` directory. Source changes detected during export cause
failure. Export is intended for a quiescent project, not as a transaction over
concurrent filesystem edits. Unchanged project state produces identical bytes.

## BM EXTENSION

The YAML `bm.okf_export` mapping has `version: 1` and `relations`, an ordered
list of original BM wikilink relations with `type`, `target`, and `context`.
This preserves typed edges and authored target spelling after links become
ordinary Markdown. Existing `bm` keys are preserved; an existing `okf_export`
key or non-mapping `bm` is a collision and fails export.

Categorized observations retain their human-readable `[category] content`
syntax, tags, context, and temporal qualifiers in the body. They are not copied
into a second metadata list. The extension declares this BM interpretation of
the body; generic OKF consumers can read it as ordinary Markdown. Relations
in metadata are authoritative for recovering BM edge types; ordinary Markdown
links alone only express untyped edges. This command does not add an importer
or switch Basic Memory's canonical syntax.

## OPTIONS

- **-p, --project** — Configured local project to export
- **--replace** — Replace an existing destination bundle
- **--json** — Output machine-readable diagnostics

## EXAMPLES

```
bm okf export ~/exports/research --project research
bm okf export ~/exports/research --project research --replace --json
bm okf check ~/exports/research
```

## SEE ALSO

- see_also [[okf-check(1)]]
