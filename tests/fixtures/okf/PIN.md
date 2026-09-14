# Upstream OKF compatibility fixture

Source: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/e6d34fd29c1c6c75ec23078e7a8191a9c8209620/okf

Revision: `e6d34fd29c1c6c75ec23078e7a8191a9c8209620`. Apache-2.0 license in LICENSE.md.
`upstream_document.py.txt` is an unmodified test-only copy of
`okf/src/reference_agent/bundle/document.py`. `crypto_bitcoin/` is the upstream
sample bundle, excluding the generated viewer HTML. Expected concepts: 9.
No upstream code is imported by the runtime package.

`upstream_invalid_log.md` is the unchanged Acme sample log: its frontmatter
violates §9 and is deliberately a negative validation fixture.
