# Markdown path links

Basic Memory indexes ordinary Markdown links to files in the same project as
`links_to` relations, alongside existing wikilinks:

```markdown
See [the guide](../guides/Getting%20Started.md#installation).
```

The destination is resolved relative to the note containing the link. A leading
`/` addresses the project root. Percent-encoded filenames are decoded; fragments
and query strings do not change which file the relation targets. Reference-style
Markdown links work too.

These are exact file paths. Basic Memory does not guess a title, add `.md`, apply
filename aliases, or search another project when the target is missing. The graph
stores a normalized project-root target such as `/guides/Getting Started.md`;
missing targets remain unresolved and can resolve when indexed later.

External URLs, `mailto:` and `file:` links, fragment-only links, paths that escape
the project, images, and links inside code do not create relations. Ordinary
Markdown links always create `links_to` edges; typed relation syntax continues to
use wikilinks. Notes with `bm_parse_semantics: false` remain graph-silent.

The authored Markdown is preserved. Existing notes gain these relations on their
next edit or reindex. This supports interoperable Markdown navigation; it does not
by itself declare full Open Knowledge Format conformance or add an export format.
