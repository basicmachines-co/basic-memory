---
title: Document Extraction
type: schema
permalink: schema/document-extraction
entity: extracted_text
version: 1
schema:
  extracted_from?: Entity, optional provenance relation on an enriched document
settings:
  validation: warn
  frontmatter:
    schema_version(enum): ["1"]
    title: string
    type(enum): [extracted_text, document]
    schema(enum): [schema/document-extraction]
    bm_parse_semantics: boolean
    source(object):
      entity_external_id: string
      file_path: string
      media_type: string
      checksum: string
    extraction(object):
      engine: string
      engine_version: string
      status(enum): [complete, partial, needs_ocr, failed]
    ingestion(object):
      stage(enum): [raw, ready, needs_review, failed]
      pipeline_version: string
      run_id: string
      input_checksum: string
---

# Document Extraction

This is the public, opt-in schema note referenced by generated extracted-text
notes. Copy it into a Basic Memory project's `schema/document-extraction.md` path
to make the schema discoverable there. Core does not install it automatically.

An extracted-text note is a derived Markdown sidecar (for example
`report.pdf.md`); its original PDF remains a separate file entity. The source identity and checksum identify the bytes used for extraction.
Extractor version, options, and pipeline inputs determine ingestion-run identity.
The trusted ingestion service owns these fields and the derived note path.

## Note type

New sidecars use `type: extracted_text`. Sidecars written before that rename use
`type: document` and remain valid. Core reads both values and never rewrites one
into the other: accepted sidecar bytes and checksums were computed with the
stored value. An enriched note keeps its raw note's type. Every sidecar names
this schema explicitly with `schema: schema/document-extraction`, so schema
resolution does not depend on the type value.

## Extraction summary

The sidecar's `extraction` group is a summary: engine, version, profile, options
hash, status, page and OCR counts, and the pages that need OCR. New sidecars do
not carry the per-page `page_map`; it lives on the ingestion-run note
(`document-ingestion-runs/<run_id>.md`). Sidecars written earlier still carry a
copy of the map, which stays valid and must equal the run note's map.

## Validation boundary

This Picoschema is a discoverability aid with advisory validation. It summarizes
required provenance groups; it is not the complete ingestion validator. In
particular, Picoschema frontmatter validation does not enforce all nested child
types or cross-field invariants.

The authoritative versioned contract is `DocumentNoteFrontmatterV1` in
[`src/basic_memory/schemas/document.py`](../../src/basic_memory/schemas/document.py).
Use that contract and the document assembly helpers at the ingestion boundary.
They validate deterministic run identity, source/input checksum agreement,
trusted citation destinations, timestamps, and the semantic parsing policy.
Optional provenance and diagnostics fields are defined there, including storage
versions, page counts, OCR diagnostics, and citation `sources`.

## Raw and enriched content

Raw and failed documents use the literal YAML boolean `bm_parse_semantics: false`.
Their bodies remain searchable, but extracted `- [category]` or `[[Target]]`
syntax does not create graph observations or relations. An `extracted_from`
relation is optional on enriched content; raw extraction does not create it.

Ready and needs-review documents enable semantic parsing after trusted assembly
has validated the structured enrichment output. A later enrichment must compare
against the exact accepted raw checksum so it cannot overwrite an intervening
human edit.

Page citations are described in [PDF page citations](../DOCUMENT_CITATIONS.md).
The portable extraction adapter and source-read/write protocols live in
[`src/basic_memory/document_ingestion`](../../src/basic_memory/document_ingestion).
This schema does not install a parser or add a local PDF import command.
