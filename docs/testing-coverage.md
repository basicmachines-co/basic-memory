## Coverage policy (practical 100%)

Basic Memory’s test suite intentionally mixes:
- unit tests (fast, deterministic)
- integration tests (real filesystem + real DB via `test-int/`)

To keep the default CI signal **stable and meaningful**, the default `pytest` coverage report targets **core library logic** and **excludes** a small set of modules that are either:
- highly environment-dependent (OS/DB tuning)
- inherently interactive (CLI)
- background-task orchestration (watchers/sync runners)
- external analytics

### What’s excluded (and why)

Coverage excludes are configured in `pyproject.toml` under `[tool.coverage.report].omit`.

Current exclusions include:
- `src/basic_memory/cli/**`: interactive wrappers; behavior is validated via higher-level tests and smoke tests.
- `src/basic_memory/db.py`: platform/backend tuning paths (SQLite/Postgres/Windows), covered by integration tests and targeted runs.
- `src/basic_memory/services/initialization.py`: startup orchestration/background tasks; covered indirectly by app/MCP entrypoints.
- `src/basic_memory/sync/sync_service.py`: heavy filesystem↔DB integration; validated in integration suite (not enforced in unit coverage).
- `src/basic_memory/telemetry.py`: external analytics; exercised lightly but excluded from strict coverage gate.
- a few thin MCP wrappers (`mcp/tools/recent_activity.py`, `mcp/tools/read_note.py`, `mcp/tools/chatgpt_tools.py`).
- `src/basic_memory/repository/postgres_search_repository.py`: covered in a separate Postgres-focused run.

### Recommended additional runs

If you want extra confidence locally/CI:
- **Postgres backend**: run integration tests with `BASIC_MEMORY_TEST_POSTGRES=1`.
- **Strict integration coverage**: run coverage on `test-int/` with Postgres enabled (separately), then combine reports if desired.


