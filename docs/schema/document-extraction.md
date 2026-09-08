---
title: Document Extraction
type: schema
permalink: schema/document-extraction
entity: document
version: 1
schema:
  extracted_from?: Entity, optional provenance relation on an enriched document
settings:
  validation: warn
  frontmatter:
    schema_version(enum): ["1"]
    title: string
    type(enum): [document]
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

This is the public, opt-in schema note referenced by generated document notes.
Copy it into a Basic Memory project's `schema/document-extraction.md` path to
make the schema discoverable there. Core does not install it automatically.

A document is a derived Markdown note; its original PDF remains a separate file
entity. The source identity and checksum identify the bytes used for extraction.
Extractor version, options, and pipeline inputs determine ingestion-run identity.
The trusted ingestion service owns these fields and the derived note path.

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
