# Compact discovery

Use compact results to choose notes before reading their bodies:

```sh
bm tool search-notes "deployment" --compact --json
bm tool build-context "notes/deployment" --compact --json
bm tool read-note "notes/deployment" --start-line 20 --end-line 60 --plain
```

`--compact` also works with `--plain` and the interactive Rich display. Output
format selection is unchanged: pipes default to JSON, while `--plain` explicitly
requests text. Omit `--compact` to retain ordinary content-bearing responses.

Search omits note bodies and matched excerpts. Context omits note and observation
bodies; observation labels become categories and their read targets become owning
file paths. IDs, relation targets, and pagination remain available in JSON.

MCP clients use `search_notes(compact=True)` and `build_context(compact=True)`.
These options reduce model-visible output, not database search or traversal work.
They do not impose a fixed token limit: titles, metadata, and the number of graph
items still affect response size. Use pagination and targeted note reads to bound
the rest of the workflow.
