# pstack evidence — bm #1853

## Principles applied
- fix-root-causes: leftover search_index_rebuilt blocks CREATE; drop it before rebuild.
- Laziness Protocol: one DROP IF EXISTS before CREATE; no broader migration rewrite.
- Prove It Works / Test Behavior: regression seeds leftover empty FTS5 table and asserts upgrade reaches script_ngrams with rows intact.
- Read poteto-mode SKILL.md this session (absolute path under pstack cache).
