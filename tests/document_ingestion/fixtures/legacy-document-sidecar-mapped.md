---
schema_version: '1'
title: report.pdf
type: document
schema: schema/document-extraction
tags:
- document
- pdf
- generated
permalink: docs/report-pdf
source:
  kind: file
  media_type: application/pdf
  entity_external_id: 11111111-1111-1111-1111-111111111111
  file_path: docs/report.pdf
  checksum: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  size_bytes: 1024
  storage_etag: etag-1
extraction:
  engine: firecrawl/pdf-inspector
  engine_version: 0.2.6
  profile: pdf-inspector-v2
  options_hash: sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
  classification: text_based
  status: complete
  extracted_at: '2026-08-01T03:01:00Z'
  duration_ms: 12
  page_count: 2
  extracted_page_count: 2
  requires_ocr: false
  ocr_page_count: 0
  pages_needing_ocr: []
  page_map:
    unit: unicode_code_point
    body_checksum: sha256:20aa908f7275e569c73036c3c16af0094bbb1b7854574b12455983fec0a71925
    body_length: 60
    pages:
    - page: 1
      start: 0
      end: 30
    - page: 2
      start: 30
      end: 60
  confidence: 1.0
  has_encoding_issues: false
  has_tables: false
  has_columns: false
ingestion:
  stage: raw
  pipeline_version: pdf-inspector-raw-v1
  run_id: a96b9b48-f2d6-5998-882c-f399af958a3e
  input_checksum: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
document:
  kind: pdf
  authors: []
bm_parse_semantics: false
---

<!-- Page 1 -->

First page.

<!-- Page 2 -->

Second page.
