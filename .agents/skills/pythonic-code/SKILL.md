---
name: pythonic-code
description: >-
  Write, refactor, and review Python for clarity, explicit behavior, local reasoning, strong
  types, and minimal abstraction. Use when creating or changing nontrivial Python, simplifying
  object-heavy or helper-heavy code, evaluating whether code is Pythonic, or reviewing Python
  maintainability in Basic Memory repositories.
---

# Pythonic Code

Write Python that makes domain behavior obvious to human and AI readers. Apply a WWGD lens:
choose the simplest correct design that feels native to Python and is easy to verify.

## Orient Before Coding

1. Read the repository's `AGENTS.md` or `CLAUDE.md` instructions.
2. Read `docs/ENGINEERING_STYLE.md` when present.
3. Read `docs/DOMAIN_MODEL.md` when the change touches domain language, ownership, identity,
   source-of-truth rules, or lifecycle behavior.
4. Read each target file completely before editing it.
5. Identify the supported Python version, configured tools, surrounding patterns, and behavior
   that must remain stable.

Let local project rules override generic style advice.

## Make Decisions In This Order

1. Preserve correctness, domain invariants, and public behavior.
2. Respect repository conventions and compatibility constraints.
3. Make data flow, control flow, errors, and side effects obvious.
4. Choose the smallest abstraction that reduces cognitive load now.
5. Use Python idioms when they clarify intent rather than merely shorten code.
6. Prove the result with types, tests, and repository tooling.

## Prefer Functions Before Hierarchies

- Start with an ordinary, fully typed function.
- Pair functions with a dataclass when related state or an operation result needs a name.
- Use callbacks, closures, or `functools.partial` when binding behavior is clearer than creating
  another object.
- Use `functools.singledispatch` only when behavior genuinely varies by the first argument's
  runtime type and open registration is an intentional extension point.
- Use a narrow `Protocol` for genuine replaceable behavior. Do not use property-only protocols to
  describe internal result data; return a concrete frozen dataclass unless callers truly require
  structural interoperability.
- Use a concrete class when identity, cohesive mutable state, lifecycle, or resource ownership
  requires one.
- Use an abstract base class only when runtime-enforced subclassing or shared skeletal behavior
  is part of the current design.

Do not replace one class hierarchy with clever functional machinery. Prefer the form with the
fewest concepts, hidden rules, and call hops.

## Keep Reasoning Local

- Keep a straightforward workflow together when top-to-bottom reading is clearest.
- Extract a helper only when its name captures a domain operation, it isolates a side effect or
  constraint, it removes meaningful duplication, or it forms a cohesive testable computation.
- Do not extract helpers merely to shorten a function.
- Treat a class dominated by private methods as a signal that behavior may belong in explicit
  module-level functions operating on typed values.
- Treat long chains of `_prepare_*`, `_resolve_*`, `_apply_*`, and `_build_*` calls as a prompt to
  reconsider the data flow or name one meaningful phase object.
- Avoid manager, factory, base, adapter, strategy, and registry abstractions with only one real
  implementation.
- Avoid dynamic registration, metaprogramming, and decorator-driven control flow unless the
  product currently needs that extension mechanism.

If extracting a helper makes the reader navigate more but understand no less, keep the logic
local.

## Write Explicit Python

- Name values after the domain concept they carry.
- Use full annotations and narrow types. Do not hide uncertainty with `Any`, broad casts,
  speculative `getattr`, or unstructured dictionaries.
- Use dataclasses for internal values and Pydantic at validation and serialization boundaries.
- Prefer direct iteration, context managers, standard-library building blocks, and simple
  comprehensions where their meaning is immediate.
- Distinguish absence from falsiness; use truth-value testing only when empty values share the
  intended meaning.
- Keep async work, resource ownership, cancellation, and cleanup visible.
- Fail fast with specific errors. Do not add silent fallbacks or broad exception handling.
- Comment decisions and constraints, not mechanics.
- Optimize measured hot paths; do not trade readability for hypothetical performance.

## Match The Requested Mode

### Write

Establish the contract and domain values first. Implement the direct path, then add only the
abstractions required by real variation, state, or boundaries.

### Refactor

Preserve observable behavior, keep the diff focused, and add or update a regression test when
the behavior is risky. Do not mechanically rewrite already-clear code to apply an idiom.

### Review

Report concrete readability, abstraction, typing, lifecycle, and domain-model risks. Explain the
smallest practical improvement. Do not edit unless the user asks for fixes.

## Verify The Result

Run the narrowest command that proves the change, then widen according to risk:

1. Focused tests for the changed behavior.
2. Formatter, linter, and type checker configured by the project.
3. Repository health, package, integration, or full gates when boundaries are affected.

Lead the final response with the outcome and verification. Explain design choices only when they
are non-obvious or materially affect future work.
