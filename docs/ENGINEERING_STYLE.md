# Basic Memory Engineering Style

Style is how we make code easier to verify. Prefer explicit, typed, local-first code that
preserves the file system as the source of truth while keeping the database, API, and MCP
surfaces in sync.

## Design Center

- Basic Memory is local-first. Markdown files are the durable source; SQLite/Postgres indexes
  are derived state that should be rebuilt or reconciled from files when needed.
- Keep the existing boundary order: CLI/MCP/API entrypoints compose dependencies, services own
  business behavior, repositories own database access, and file services own filesystem writes.
- MCP tools should remain atomic and composable. They should call API routers through typed MCP
  clients, not reach around into services.
- Prefer small, explicit abstractions that match a real domain boundary. Avoid object
  hierarchies when a function, dataclass, type alias, or protocol describes the concept better.

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
