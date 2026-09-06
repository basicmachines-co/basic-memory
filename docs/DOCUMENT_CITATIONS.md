# PDF page citations

Document enrichment can attach an optional `locator` to an observation:

```json
{
  "category": "fact",
  "content": "Revenue increased.",
  "locator": {"page": 2, "page_label": "iv"}
}
```

`page` is the one-based physical PDF page, not the printed page label. Trusted
assembly checks it against the extraction's page count and derives the destination
from the source file path. The agent does not supply a citation URL.

The resulting document retains its existing `source` checksum and storage-version
provenance and adds OKF-compatible `sources` entries and Markdown footnotes:

```yaml
sources:
  - id: document-page-2
    resource: /docs/report.pdf#page=2
    title: report.pdf, p. iv
    locator:
      page: 2
      page_label: iv
```

```markdown
## Observations

- [fact] Revenue increased. [^document-page-2]

[^document-page-2]: [PDF page 2](/docs/report.pdf#page=2)
```

The footnote label joins to `sources[].id`, not the entry's array position. IDs are
scoped to this document note and its single trusted source PDF. Multiple
observations on the same page share an entry. Reordering observations or sources
does not change the target. Conflicting printed labels for the same page are
rejected. Uncited documents retain their existing serialized shape.

The leading slash denotes a project/bundle-root-relative resource. Consumers must
resolve it in that scope; it is not a new Cloud HTTP route. The fragment requests
the physical page in a supporting PDF viewer. The existing source checksum and
storage version identify the bytes used for extraction; the link itself does not
retrieve a historical version or automatically detect replacement of the PDF.

## Scope and prior art

This implements page-level addressing for #1366 and the citation convention from
the amended SPEC-89. It does not implement an extraction offset map, quote
highlighting, generic OKF conformance (#1246), evidence watermarks, or contradiction
detection. Cloud prompt adoption and viewer routing are separate integration work.

- [RFC 8118, section 3](https://www.rfc-editor.org/rfc/rfc8118.html#section-3)
  defines the PDF `page=N` fragment with one-based page numbering.
- [OKF provenance sources](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md#51-provenance-sources)
  provides `sources[].id`, resource references, and footnote-label joins.
- [Zotero PDF reader](https://www.zotero.org/support/pdf_reader) demonstrates the
  annotation/note-to-source-page navigation experience.
- [W3C TextQuoteSelector](https://www.w3.org/TR/annotation-model/#text-quote-selector)
  is prior art for a later, more precise exact-text/context selector; no W3C
  selector is implemented here.
