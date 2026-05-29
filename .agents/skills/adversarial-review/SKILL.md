---
name: adversarial-review
description: Cross-vendor adversarial code review of the current branch. Two different model families (Claude + Codex/GPT) review the diff independently, then try to refute each other's findings; survivors are reported by confidence. Runs from either Claude Code or Codex. Use when the user asks for an adversarial review, a cross-model / second-opinion review, or wants high-confidence findings before merging. Report-only — never auto-applies fixes.
license: MIT
---

# Adversarial code review

Two reviewers from **different model families** — **Claude** and **Codex/GPT** — review the
same diff independently, then each tries to **refute** the other's findings. A finding's
confidence comes from whether it survives that cross-examination. This kills the two failure
modes of solo LLM review: self-ratification (a model won't critique its own work) and
confident false positives.

## You are the orchestrator — and one of the two reviewers

This skill runs from **either** Claude Code **or** Codex. First, **identify which model
family you are** (Claude or Codex/GPT). Then:

- **You** are reviewer #1. You review **natively**, in this session, using your own tools.
- **The other family** is reviewer #2. You invoke it as a **subprocess CLI** for an
  independent pass: a fresh process, no shared context — that independence is the point.

The CLI for "the other model":

| If you are… | Invoke the other via… |
|-------------|------------------------|
| **Claude**  | `codex exec` (GPT)     |
| **Codex**   | `claude -p` (Claude)   |

Everything else in the flow is symmetric. Resolve the `prompts/` and `schemas/` paths
below relative to **this skill's own directory** (where this SKILL.md lives).

## Inputs

Two independent, optional inputs:

- `BASE` — the ref to diff against. Default `main`.
- `SCOPE` — a pathspec to narrow the review (e.g. `src/basic_memory`). Default: none (whole diff).

These are separate: a ref and a pathspec are not interchangeable. Build the **canonical diff
command** once in preflight and reuse it everywhere below — never re-spell the diff inline
(the scattered, inconsistent spelling is what broke earlier). Build it as an **argv array**,
not a string, so a `$SCOPE` containing spaces or glob characters survives intact:

```bash
BASE="${BASE:-main}"
DIFF=(git diff "$BASE...HEAD")          # argv array — never a scalar string
[ -n "$SCOPE" ] && DIFF+=(-- "$SCOPE")  # pathspec stays one argument even with spaces
DIFF_STR=$(printf '%q ' "${DIFF[@]}")   # shell-quoted rendering, for embedding in a prompt
```

To **run** it, use `"${DIFF[@]}"` (quoted, no word-splitting). To **embed** it as text inside
a subprocess prompt, use `$DIFF_STR`.

## Preflight

0. Set `SKILL_DIR` to the directory this SKILL.md lives in. Canonical location is
   `.agents/skills/adversarial-review` (the shared agent-skills store); Claude Code reaches it
   via the `.claude/skills/adversarial-review` symlink, Codex via its own skills path. The
   `prompts/` and `schemas/` subdirs are siblings of this file in every case.
1. Confirm the *other* model's CLI is on PATH (`codex` if you're Claude, `claude` if you're
   Codex). If it's missing, tell the user the panel falls back to single-model (which loses
   the cross-vendor benefit) and ask whether to proceed or stop.
2. Run `"${DIFF[@]}"`. If it prints nothing, report "nothing to review against $BASE"
   (mention `$SCOPE` if set) and stop.
3. `RUN=$(mktemp -d)` — scratch dir for the other model's output. Transient, never committed.
   No persisted artifacts, no state file.

## Phase 0 — Deterministic gates (before the models)

Models are statistically blind to negation ("never do X"). Enforce mechanical house rules
with tools, not prompts, and treat hits as high-confidence facts (reported separately from
model findings):

- `just lint` and `just typecheck` if the diff touches `src/`.
- Grep the diff for catchable house-rule violations: `getattr(.*,.*,` defaults, bare
  `except:` / `except Exception: pass`, function-scope imports.

## Phase 1 — Independent review (you + the other model, concurrently)

Both reviewers get the same brief: `prompts/review.md` + the repo's `CLAUDE.md` house rules,
reviewing the diff from `"${DIFF[@]}"`. Both emit findings matching `schemas/findings.schema.json`.

**Your native pass:** review as yourself, following `prompts/review.md`. Hold your findings
as that JSON shape.

**The other model's pass** — run, from the repo root, the row that matches you:

Always redirect `codex` stdin from `/dev/null` — if stdin is a pipe (e.g. the call gets
backgrounded), `codex exec` blocks "Reading additional input from stdin..." and fails.

```bash
# You are Claude → run Codex:
codex exec -s read-only \
  --output-schema "$SKILL_DIR/schemas/findings.schema.json" \
  -o "$RUN/other_findings.json" \
  "$(cat "$SKILL_DIR/prompts/review.md")

Review the diff: $DIFF_STR" </dev/null

# You are Codex → run Claude (read-only via plan mode; parse the JSON block it returns):
claude -p --permission-mode plan --output-format json \
  "$(cat "$SKILL_DIR/prompts/review.md")

Review the diff: $DIFF_STR
Return ONLY a JSON object matching this schema:
$(cat "$SKILL_DIR/schemas/findings.schema.json")" </dev/null > "$RUN/other_raw.json"
# claude --output-format json output shape varies by CLI version: it may be a JSON ARRAY
# of event objects, OR a single result object. Normalize before reading: if it's an array,
# take the element with type=='result'; otherwise use the object as-is. Then read its
# .result string, strip the ```json fence if present, and parse that.
# (Verified empirically: the CLI in this environment emits the array form.)
```

> Runtime note for Codex orchestrating: `claude -p` needs network access, which Codex's
> default sandbox blocks. Run it from a Codex session whose project is trusted with network
> allowed (or approve the `claude` call when prompted). Keep Codex's own sandbox on — do not
> bypass it just to reach the network.

Tag each finding with its origin (`claude` / `codex`).

## Phase 2 — Cross-refute

Each model tries to refute the *other's* findings, per `prompts/refute.md`
(verdicts match `schemas/verdicts.schema.json`).

- **You** refute the other model's findings natively.
- **The other model** refutes *your* findings — invoke it again the same way (swap
  `prompts/review.md` for `prompts/refute.md`, append your findings JSON **and `$DIFF_STR`**
  so it judges against the right base and scope, and for Codex use
  `--output-schema "$SKILL_DIR/schemas/verdicts.schema.json"`).

Match verdicts to findings by `id`.

## Phase 3 — Synthesize and report (no auto-fix)

Merge, dedupe (same file + overlapping lines + same root cause = one finding), assign
confidence from provenance:

- **High** — both models raised it independently, OR one raised it and the other upheld it.
- **Medium** — one raised it; the other could not refute it but did not independently find it.
- **Low / contested** — one raised it and the other **refuted** it. Keep it, show both sides,
  let the human judge. Never silently drop a contested finding.
- Deterministic-gate hits are reported as facts, separate from the model panel.

Rank by `severity × confidence`. Present a compact table: `severity | confidence | file:line
| claim | found-by / upheld-or-refuted-by`. Expand the high-confidence ones with `why` and
any suggested fix.

End by asking which findings, if any, to fix. **Do not edit code until the user picks.**
Convergence between the models is not correctness — your job is to surface a ranked,
cross-examined list, not to declare the branch clean.

## Deliberately NOT done

- No loop-until-both-agree (models converge by going silent, not by being right).
- No persisted artifacts / state machine — the scratch dir is thrown away.
- No auto-applying fixes.
