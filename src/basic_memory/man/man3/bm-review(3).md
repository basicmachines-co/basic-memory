---
title: bm-review(3)
type: manpage
section: 3
name: bm-review
summary: comment on, suggest changes to, reply to, resolve, accept or reject review items on a note (hosted server only)
generated: hand
tool: bm_review
---

# bm-review(3)

## NAME

**bm-review** — comment on, suggest changes to, reply to, resolve, accept or reject review items on a note (hosted server only)

## SYNOPSIS

```
bm_review(identifier, command, project, anchor=None, body=None,
          suggestion_kind=None, replacement=None, review_id=None,
          expected_checksum=None)
```

## PARAMETERS

- **identifier** (string, required) — The note: a file path, permalink, `memory://` URL, exact title, or external id.
- **command** (required) — What to do: `comment`, `suggest`, `reply`, `resolve`, `accept` or `reject`.
- **project** (string, required) — Project name, `workspace/project`, or project external id.
- **anchor** (object | null, optional, default: None) — Where in the prose to attach a `comment` or `suggest`. An object with `exact` (required: exact single-line note text, copied from bm_read(3), without CriticMarkup markers), and optional `prefix` and `suffix` (exact text just before and after, to disambiguate) and `occurrence` (1-based, after prefix and suffix filtering).
- **body** (string | null, optional, default: None) — The text of a `comment` or `reply`. For `suggest`, an optional reason, saved as the first reply on the suggestion thread (it must not be empty if given). For `resolve`, an optional closing reply.
- **suggestion_kind** (`addition` | `deletion` | `replacement` | null, optional, default: None) — The kind of `suggest`.
- **replacement** (string | null, optional, default: None) — The proposed text for an `addition` or `replacement`. Omit for a `deletion`.
- **review_id** (string | null, optional, default: None) — The thread to act on, for `reply`, `resolve`, `accept` and `reject`.
- **expected_checksum** (string | null, optional, default: None) — The revision checksum returned by bm_read(3). Required for `accept` and `reject`; optional for the other commands.

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server. A local client will not find this tool.

`bm_review` works on the review threads of one existing Markdown note. Call
bm_read(3) first: it shows the note's prose and its open review threads.
Then give one `command` per call:

- **comment** (`anchor`, `body`): attach a comment to a passage.
- **suggest** (`anchor`, `suggestion_kind`, and `replacement` unless the kind
  is `deletion`; optional `body`): propose an addition, deletion or
  replacement. `body` is the reason for the change, saved as the first reply
  on the suggestion thread.
- **reply** (`review_id`, `body`): answer a thread.
- **resolve** (`review_id`, optional `body`): close a comment thread, with an
  optional closing reply.
- **accept** / **reject** (`review_id`, `expected_checksum`): apply or drop a
  pending suggestion.

Do not write CriticMarkup yourself. The tool writes it, attributes each new
record to the detected MCP client, and saves each command atomically with
checksum conflict protection. A command that is missing a field it needs is
refused before anything is saved.

The reply has `status` `review_updated`, the note's `title`, `permalink`,
`file_path`, `project` and `project_id`, the ids it created (`comment_ids`,
`suggestion_ids`, `reply_id`), the `review_id` acted on, and the note's new
`revision_checksum`.

## EXAMPLES

```
bm_review(identifier="Plan", command="comment", project="main",
          anchor={"exact": "this sentence"}, body="Needs a source.")

bm_review(identifier="Plan", command="suggest", project="main",
          anchor={"exact": "ship in May"}, suggestion_kind="replacement",
          replacement="ship in June",
          body="The vendor slipped a month.")

bm_review(identifier="Plan", command="accept", project="main",
          review_id="<id from bm_read>", expected_checksum="<checksum from bm_read>")
```

## GOTCHAS

- `accept` and `reject` change the note's text, so they refuse to run
  without `expected_checksum`.
- If `expected_checksum` no longer matches, the note changed since bm_read(3).
  Read it again before applying review work.
- An `anchor.exact` must stay on one line. A `comment` anchor cannot contain
  `==}`, and a `suggest` anchor cannot contain `{` or `}`.
- A deletion takes no `replacement`; an addition or replacement needs one.
  An addition is inserted right after `anchor.exact`.
- It works only on existing Markdown notes, in the selected project.
- Unlike the note tools, it is marked destructive: `accept` rewrites the note.

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-edit(3)]]
- see_also [[bm-man(3)]]
