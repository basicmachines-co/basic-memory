# Basic Memory Engineering Style

Style is how we make code easier to verify. Prefer explicit, typed, local-first code that keeps
Markdown as the canonical product representation while the file materialization, database, API,
and MCP surfaces stay in sync.

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
