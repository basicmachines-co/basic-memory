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
the amended SPEC-89. It does not implement quote
highlighting, generic OKF conformance (#1246), evidence watermarks, or contradiction
detection. Cloud prompt adoption and viewer routing are separate integration work.

## Extraction page map

New pdf-inspector extractions retain `extraction.page_map` on both the document
and ingestion-run note. Each entry has a one-based physical `page` and half-open
`start`/`end` offsets measured in Unicode code points, as in Python string slices.
The ranges partition the normalized raw Markdown body, including page markers;
inter-page separators belong to the preceding page. OCR-only pages retain a
range for their marker. Offsets exclude frontmatter and include the canonical
final newline.

The map carries `body_length` and a SHA-256 checksum of that body's UTF-8 bytes.
`DocumentPageMapV1.resolve_span(body, start=..., end=...)` verifies the exact body
before returning the physical pages intersecting a nonempty span. It rejects
rewritten text and invalid bounds. After enrichment the map still describes the
raw extraction revision, never the new agent-written body. Retrieve that raw
revision before resolving a span; the source PDF checksum identifies a different
artifact and cannot substitute for the body checksum.

Older extractions omit the optional map and retain their serialized shape. The
pdf-inspector extraction profile is now `pdf-inspector-v2`, yielding a new run
identity when the same PDF is explicitly re-extracted; existing notes are not
backfilled automatically. Other extraction providers may supply the same
parser-neutral map contract.

- [RFC 8118, section 3](https://www.rfc-editor.org/rfc/rfc8118.html#section-3)
  defines the PDF `page=N` fragment with one-based page numbering.
- [OKF provenance sources](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md#51-provenance-sources)
  provides `sources[].id`, resource references, and footnote-label joins.
- [Zotero PDF reader](https://www.zotero.org/support/pdf_reader) demonstrates the
  annotation/note-to-source-page navigation experience.
- [W3C TextQuoteSelector](https://www.w3.org/TR/annotation-model/#text-quote-selector)
  is prior art for a later, more precise exact-text/context selector; no W3C
  selector is implemented here.
