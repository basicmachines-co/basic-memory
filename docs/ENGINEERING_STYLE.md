# Basic Memory Engineering Style

Style is how we make code easier to verify. Prefer explicit, typed, local-first code that keeps
Markdown as the canonical product representation while the file materialization, database, API,
and MCP surfaces stay in sync.

Our default design method is **Constructive Domain Modeling**: describe the states and outcomes
the product supports, construct those values at trusted boundaries, and let their types carry
obligations through the program.

## Design Center

- Basic Memory is local-first. In local flows, Markdown files are the durable source and
  SQLite/Postgres indexes are derived state. DB-first and cloud-style writes may record the exact
  accepted Markdown in `NoteContent` before materializing the file. Follow
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the authority and projection rules in each phase.
- Keep the existing boundary order: CLI/MCP/API entrypoints compose dependencies, services own
  business behavior, repositories own database access, and file services own filesystem writes.
- MCP tools should remain atomic and composable. They should call API routers through typed MCP
  clients, not reach around into services.
- Prefer small, explicit abstractions that match a real domain boundary. Avoid object
  hierarchies when a function, dataclass, type alias, or protocol describes the concept better.

## Constructive Domain Modeling

Constructive Domain Modeling defines a domain by its **positive space**: the values that can be
constructed and handled correctly. It is a practical Python application of product types, sum
types, parsing, and exhaustive handling—not a mandate to eliminate classes or exceptions.

- Model one valid state with a small value whose required fields are always present. Model
  meaningful alternatives as a closed union of values, rather than one record with a status
  string and mutually conditional optional fields.
- Prefer frozen dataclasses for internal domain values and Python 3.12 `type` aliases for closed
  unions. At JSON-facing boundaries, use Pydantic discriminated unions when runtime validation,
  serialization, or generated schemas need to preserve the alternatives.
- Parse and classify external data once at the boundary. After construction, internal functions
  should accept the domain value instead of repeatedly validating the same invariant.
- Consume closed unions with an explicit `match`. Use `typing.assert_never` when it lets the type
  checker prove that every variant is handled; avoid a catch-all branch that silently absorbs a
  future domain case.
- Make functions total over their declared input when practical. If a recoverable case is part of
  normal operation, strengthen the input type or include that case in a structured return type
  instead of raising a "should never happen" exception deep in the workflow.
- Return explicit variants for expected domain outcomes when callers can make a meaningful
  decision about them. Keep exceptions for broken invariants, cancellation, and unpredictable
  resource failures such as filesystem, network, queue, or database errors.
- Choose the least precise model that removes a real unsupported state. Do not introduce wrapper
  types, Result types, or elaborate unions merely to make the type graph look stronger.
- Before reshaping persisted state, trace every writer and compatibility constraint. ORM rows and
  old payloads may need a parser that converts their broad storage shape into a narrower domain
  value.

For example, prefer separate `Completed(result)` and `Failed(reason)` values joined by a
`type OperationOutcome = Completed | Failed` alias over a single `Operation` record where
`result` and `reason` are both optional and their validity depends on a status string.

## Functions Before Hierarchies

- Start with an ordinary, fully typed function. Pair functions with a dataclass when related
  state, inputs, or results need a name.
- Use callbacks, closures, or `functools.partial` when binding behavior produces a clearer call
  site than another object. Use `functools.singledispatch` only when behavior genuinely varies by
  the first argument's runtime type and open registration is an intentional extension point.
- Use a narrow `Protocol` for a capability contract. Prefer structural typing over requiring
  implementations to inherit from a shared base.
- Use a concrete class when identity, cohesive mutable state, lifecycle, or resource ownership
  requires one. Keep orchestration in the class and move independent computation into functions.
- Reserve abstract base classes for runtime-enforced extension frameworks or shared skeletal
  behavior that exists now. Do not introduce inheritance for hypothetical implementations.
- Do not replace class hierarchies with dense functional machinery. Prefer the design with the
  fewest concepts, hidden rules, and call hops.

## Types And Data

- Use full type annotations and Python 3.12 syntax. Introduce `type` aliases for repeated
  structured shapes, callback signatures, or domain concepts that would otherwise become
  anonymous `dict[str, Any]` values.
- Use dataclasses for internal values, operation inputs, and service results. Prefer
  `frozen=True` when the value should not change and `slots=True` when identity/dynamic
  attributes are not needed.
- Use Pydantic v2 at boundaries that validate, serialize, or deserialize data: API payloads,
  CLI/MCP schemas, configuration, and persistence-adjacent schemas.
- Use narrow `Protocol`s when a caller needs a capability rather than a concrete repository or
  service. Keep protocols small enough that fake implementations in tests are obvious.
- Avoid speculative `getattr`, broad casts, or `Any` as a way to paper over uncertainty. Read
  the model or schema definition and make the type relationship explicit.

## Control Flow And Resources

- Fail fast when an invariant is broken. Do not swallow exceptions, add warning-only error
  handling, or introduce fallback behavior unless the user explicitly agrees to that behavior.
- Do not use exceptions as ordinary branching for expected domain outcomes. Translate a typed
  outcome to HTTP, CLI, or MCP errors at the outer adapter that owns that presentation contract.
- Keep control flow simple and close to the domain decision. Push `if` statements up into the
  function that owns orchestration; keep leaf helpers focused on computation or one side effect.
- Make async/resource boundaries visible with context managers and explicit lifecycles. Do not
  start background work without a clear owner, cancellation story, and verification path.
- Keep file mutations centralized through the existing file utilities/services so checksum,
  atomic write, and index synchronization behavior stays coherent.

## Local Reasoning And Abstraction Budget

- Keep a straightforward workflow together when reading it top-to-bottom is clearer than
  navigating helpers. Do not split code merely to reduce function length.
- Extract a helper when its name captures a domain operation, it isolates a side effect or
  constraint, it removes meaningful duplication, or it forms a cohesive testable computation.
- Treat a class dominated by private methods as a signal that its behavior may belong in
  module-level functions operating on typed values.
- Treat long chains of `_prepare_*`, `_resolve_*`, `_apply_*`, and `_build_*` helpers as a prompt
  to simplify the data flow or introduce one meaningful phase value. Private helpers are useful;
  private-helper sprawl is not.
- Avoid manager, factory, base, adapter, strategy, and registry abstractions with only one real
  implementation. Add extension points when a second behavior or active integration requires
  them, not in anticipation of one.
- Avoid dynamic registration, metaprogramming, and decorator-driven control flow unless the
  product requires that mechanism and the lifecycle remains explicit.
- Make behavior traceable from an entrypoint to its domain decision and side effects without
  reconstructing implicit state across many files. Optimize for human and AI readers alike.
- Every abstraction should reduce the number of concepts or call paths a reader must hold. If a
  helper makes the reader navigate more but understand no less, keep the logic local.

## Testing And Verification

- Use evidence-first testing, not mechanical TDD. For bugs and risky behavior, add or update a
  regression test that would catch the failure. For small documentation-only edits, use the
  relevant doc/repo hygiene checks.
- Prefer tests that exercise real code paths. Use mocks, doubles, or `monkeypatch` only when
  the external boundary would be slow, nondeterministic, or impossible to trigger directly.
- Test every meaningful domain variant and the boundary that constructs it. Let the type checker
  enforce exhaustive consumers; use runtime tests for behavior and compatibility, not to
  compensate for an unnecessarily broad internal state model.
- Keep coverage at 100% for new code. Use `# pragma: no cover` only for code that would require
  disproportionate mocking and is covered through an integration or runtime path.
- Start with targeted commands, then widen as risk grows: focused pytest, `just fast-check`,
  `just doctor`, package checks for agent packaging changes, and full SQLite/Postgres gates
  when behavior crosses shared boundaries.

## Comments And Names

- Name values after the domain concept they carry: project, entity, permalink, tenant, route,
  checksum, observation, relation, batch, or index state.
- Comments should say why a branch, invariant, retry, lifecycle, or compatibility constraint
  exists. Section headers are useful when a function or file has clear phases.
- Avoid comments that restate the code. If a comment cannot explain a decision, simplify the
  code or improve the name instead.
