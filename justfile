# Basic Memory - Modern Command Runner

TESTMON_FLAGS := env_var_or_default("BASIC_MEMORY_TESTMON_FLAGS", "--import-mode=importlib --testmon-noselect")
TESTMON_SELECT_FLAGS := env_var_or_default("BASIC_MEMORY_TESTMON_SELECT_FLAGS", "--import-mode=importlib --testmon --testmon-forceselect")
TESTMON_REFRESH_FLAGS := env_var_or_default("BASIC_MEMORY_TESTMON_REFRESH_FLAGS", "--import-mode=importlib --testmon-noselect")
# CI shards the Postgres unit suite across parallel jobs via pytest-split
# (e.g. "--splits 3 --group 2"). Empty locally.
PYTEST_SPLIT_FLAGS := env_var_or_default("BASIC_MEMORY_PYTEST_SPLIT_FLAGS", "")

# Install dependencies
install:
    uv sync
    @echo ""
    @echo "💡 Remember to activate the virtual environment by running: source .venv/bin/activate"

# ==============================================================================
# DATABASE BACKEND TESTING
# ==============================================================================
# Basic Memory supports dual database backends (SQLite and Postgres).
# By default, tests run against SQLite (fast, no dependencies).
# Set BASIC_MEMORY_TEST_POSTGRES=1 to run against Postgres (uses testcontainers).
#
# Quick Start:
#   just test              # Run all tests against SQLite (default)
#   just test-sqlite       # Run all tests against SQLite
#   just test-postgres     # Run all tests against Postgres (testcontainers)
#   just test-unit-sqlite  # Run unit tests against SQLite
#   just test-unit-postgres # Run unit tests against Postgres
#   just test-int-sqlite   # Run integration tests against SQLite
#   just test-int-postgres # Run integration tests against Postgres
#
# CI runs both in parallel for faster feedback.
# ==============================================================================

# Run all tests against SQLite and Postgres
test: test-sqlite test-postgres

# Run all tests against SQLite
test-sqlite: test-unit-sqlite test-int-sqlite

# Run all tests against Postgres (uses testcontainers)
test-postgres: test-unit-postgres test-int-postgres

# Run unit tests against SQLite
test-unit-sqlite: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=unit-sqlite tests

# Run unit tests against Postgres
# Exit code 5 (no tests collected) is success: a testmon-selected PR build can
# leave a pytest-split shard empty.
test-unit-postgres: testmon-seed
    #!/usr/bin/env bash
    set -euo pipefail
    BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} {{PYTEST_SPLIT_FLAGS}} --testmon-env=unit-postgres tests || test $? -eq 5

# Run integration tests against SQLite (excludes semantic tests and on-demand benchmarks —
# use just test-semantic / run benchmark files explicitly)
test-int-sqlite: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=int-sqlite -m "not semantic and not benchmark" test-int

# Run integration tests against Postgres
# Note: Uses timeout due to FastMCP Client + asyncpg cleanup hang (tests pass, process hangs on exit)
# See: https://github.com/jlowin/fastmcp/issues/1311
test-int-postgres: testmon-seed
    #!/usr/bin/env bash
    set -euo pipefail
    # Use gtimeout (macOS/Homebrew) or timeout (Linux)
    TIMEOUT_CMD=$(command -v gtimeout || command -v timeout || echo "")
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD --signal=KILL 600 bash -c 'BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=int-postgres -m "not semantic and not benchmark" test-int' || test $? -eq 137
    else
        echo "⚠️  No timeout command found, running without timeout..."
        BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=int-postgres -m "not semantic and not benchmark" test-int
    fi

# Run tests impacted by recent changes (requires pytest-testmon)
# Pass paths or node ids after `just testmon` to limit the candidate set further.
testmon *args: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_SELECT_FLAGS}} --testmon-env=local {{args}}

# Seed pytest-testmon data into this worktree from the shared Git cache.
testmon-seed:
    uv run python scripts/testmon_cache.py seed

# Refresh the shared pytest-testmon cache from a full backend test run.
testmon-refresh:
    #!/usr/bin/env bash
    set -euo pipefail
    BASIC_MEMORY_TESTMON_FLAGS="{{TESTMON_REFRESH_FLAGS}}" just test
    uv run python scripts/testmon_cache.py refresh

# Show local and shared pytest-testmon cache locations.
testmon-status:
    uv run python scripts/testmon_cache.py status

# Run MCP smoke test (fast end-to-end loop)
test-smoke: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=smoke -m smoke test-int/mcp/test_smoke_integration.py

# Fast local loop: lint, format, typecheck, impacted tests via pytest-testmon
fast-check:
    just fix
    just format
    just typecheck
    just testmon

# Fast local loop with live OpenAI-backed checks disabled.
fast-check-no-openai:
    OPENAI_API_KEY= just fast-check

# ==============================================================================
# Runtime / Event Indexing Refactor
# ==============================================================================

# Focused provider-neutral project-index orchestration surface tests.
project-index-surface-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_project_index_surface.py

# Focused provider-neutral project-index workflow tests.
project-index-workflow-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_project_index_workflow.py

# Focused provider-neutral project-index coordinator tests.
project-index-runner-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_project_index_runner.py

# Focused local project-index adapter tests.
local-project-index-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py

# Focused local project-index directory delete parity test.
local-project-index-directory-delete-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_directory_delete_removes_notes_and_repairs_survivors

# Focused local project-index duplicate permalink parity test.
local-project-index-permalink-conflict-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_duplicate_permalink_update

# Focused local project-index frontmatter policy parity tests.
local-project-index-frontmatter-policy-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_does_not_add_frontmatter_when_disabled \
        tests/index/test_local_project_index.py::test_local_project_index_writes_frontmatter_when_enabled_even_if_permalinks_disabled

# Focused local project-index relation resolution parity test.
local-project-index-relation-parity-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_order_dependent_relations_after_batches

# Focused per-file indexing runner/model tests.
file-index-runner-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_index_file_runner.py \
        tests/indexing/test_file_indexer.py \
        tests/indexing/test_models.py

# Focused file-batch indexing runner/payload tests.
file-index-batch-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_file_batch_runner.py \
        tests/indexing/test_job_payloads.py

# Focused startup wiring for local project-index fanout.
local-project-index-startup-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/services/test_initialization.py::test_initialize_file_sync_uses_project_index_runtime_for_initial_sync_by_default \
        tests/services/test_initialization.py::test_initialize_file_sync_uses_legacy_sync_when_event_index_disabled

# Focused project-wide indexing orchestration surface tests.
project-index-contract-test: project-index-surface-test project-index-workflow-test project-index-runner-test local-project-index-test local-project-index-startup-test

# Focused event-based indexing contract tests for the cloud/core extraction loop.
local-event-index-regular-file-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_regular_file_parity.py \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_indexes_regular_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_deletes_missing_regular_file

# Focused local event-index relation cleanup parity test.
local-event-index-relation-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_regular_file_parity.py::test_local_event_index_deletes_regular_file_relation_target_and_repairs_search

# Focused event-based indexing contract tests for the cloud/core extraction loop.
event-index-contract-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/test_runtime_storage_events.py \
        tests/indexing/test_external_file_delete_runner.py \
        tests/index/test_filesystem_events.py \
        tests/index/test_inline_storage_event_processor.py \
        tests/index/test_local_watch_ignore_parity.py \
        tests/index/test_local_watch_regular_file_parity.py \
        tests/index/test_local_watch_orchestration.py \
        tests/index/test_repository_storage_event_project_resolution.py \
        tests/index/test_storage_event_operation_processor.py \
        tests/index/test_storage_event_orchestration.py \
        tests/sync/test_watch_service.py::test_handle_changes_can_route_through_event_index_runtime \
        tests/sync/test_watch_service.py::test_handle_changes_with_event_index_runtime_records_failed_counts \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_indexes_markdown_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_updates_existing_markdown_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_coalesces_add_and_modify \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_indexes_multiple_files_once \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_treats_delete_add_same_path_as_update \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_handles_atomic_and_true_deletes \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_indexes_regular_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_deletes_missing_regular_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_deletes_missing_markdown_file \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_skips_deleted_project \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_preserves_move_identity \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_collapses_rapid_move \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_skips_directory_rename_events \
        tests/sync/test_watch_service.py::test_handle_changes_with_local_event_index_runtime_resolves_relations_after_index \
        tests/services/test_initialization.py::test_initialize_file_sync_wires_event_index_runtime_by_default

# Focused parity loop for local project scans and shared storage-event routing.
event-index-parity-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/test_runtime.py::TestRuntimeContracts::test_runtime_storage_event_operation_plans_index_delete_and_skip_work \
        tests/test_runtime_observed_index_files.py \
        tests/index/test_local_project_index.py \
        tests/index/test_filesystem_events.py \
        tests/index/test_storage_event_operation_processor.py \
        tests/index/test_storage_event_orchestration.py

# Focused indexing contract suite for the cloud/core extraction loop.
index-contract-test: file-index-runner-test file-index-batch-test project-index-contract-test event-index-contract-test

# Focused core contract suite used by the basic-memory-cloud runtime refactor loop.
runtime-refactor-contract-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_accepted_note_write_runner.py \
        tests/indexing/test_accepted_note_enqueue_runner.py \
        tests/indexing/test_note_content_read_repair_runner.py \
        tests/runtime/test_accepted_note_response_planning.py \
        tests/runtime/test_deleted_note_response.py \
        tests/runtime/test_pending_note_materialization.py \
        tests/runtime/test_note_content_read_planning.py
    just index-contract-test

# Reset Postgres test database (drops and recreates schema)
# Useful when Alembic migration state gets out of sync during development
# Uses credentials from docker-compose-postgres.yml
postgres-reset:
    docker exec basic-memory-postgres psql -U ${POSTGRES_USER:-basic_memory_user} -d ${POSTGRES_TEST_DB:-basic_memory_test} -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    @echo "✅ Postgres test database reset"

# Run Alembic migrations manually against Postgres test database
# Useful for debugging migration issues
# Uses credentials from docker-compose-postgres.yml (can override with env vars)
postgres-migrate:
    @cd src/basic_memory/alembic && \
    BASIC_MEMORY_DATABASE_BACKEND=postgres \
    BASIC_MEMORY_DATABASE_URL=${POSTGRES_TEST_URL:-postgresql+asyncpg://basic_memory_user:dev_password@localhost:5433/basic_memory_test} \
    uv run alembic upgrade head
    @echo "✅ Migrations applied to Postgres test database"

# Run Windows-specific tests only (only works on Windows platform)
# These tests verify Windows-specific database optimizations (locking mode, NullPool)
# Will be skipped automatically on non-Windows platforms
test-windows: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=windows -m windows tests test-int

# Run benchmark tests only (performance testing)
# These are slow tests that measure sync performance with various file counts
# Excluded from default test runs to keep CI fast
test-benchmark: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=benchmark -m benchmark tests test-int

# Run semantic search quality benchmarks (all combos)
test-semantic: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=semantic -m semantic test-int/semantic/

# Run semantic benchmarks with JSON artifact output, then show report
test-semantic-report:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_BENCHMARK_OUTPUT=.benchmarks/semantic-quality.jsonl uv run pytest -p pytest_mock -v -s --no-cov -m semantic test-int/semantic/
    uv run python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl

# Run opt-in live LiteLLM provider checks against configured external APIs
test-litellm-live *args:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_RUN_LITELLM_INTEGRATION=1 PYTHONPATH=test-int:src uv run python -m semantic.litellm_live_harness {{args}}

# Run semantic benchmarks (Postgres combos only)
test-semantic-postgres: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=semantic-postgres -m semantic -k postgres test-int/semantic/

# View semantic benchmark results (rich formatted table)
# Usage: just semantic-report [--filter-combo sqlite] [--filter-suite paraphrase] [--sort-by avg_latency_ms]
semantic-report *args:
    uv run python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl {{args}}

# Compare two search benchmark JSONL outputs
# Usage:
#   just benchmark-compare .benchmarks/search-baseline.jsonl .benchmarks/search-candidate.jsonl
#   just benchmark-compare .benchmarks/search-baseline.jsonl .benchmarks/search-candidate.jsonl --format markdown --show-missing
benchmark-compare baseline candidate *args:
    uv run python test-int/compare_search_benchmarks.py "{{baseline}}" "{{candidate}}" --format table {{args}}

# Run all tests including Windows, Postgres, and Benchmarks (for CI/comprehensive testing)
# Use this before releasing to ensure everything works across all backends and platforms
test-all: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_FLAGS}} --testmon-env=all tests test-int

# Generate HTML coverage report
coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    
    uv run coverage erase
    
    echo "🔎 Coverage (SQLite)..."
    BASIC_MEMORY_ENV=test uv run coverage run --source=basic_memory -m pytest -p pytest_mock -v --no-cov tests test-int
    
    echo "🔎 Coverage (Postgres via testcontainers)..."
    # Note: Uses timeout due to FastMCP Client + asyncpg cleanup hang (tests pass, process hangs on exit)
    # See: https://github.com/jlowin/fastmcp/issues/1311
    TIMEOUT_CMD=$(command -v gtimeout || command -v timeout || echo "")
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD --signal=KILL 600 bash -c 'BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run coverage run --source=basic_memory -m pytest -p pytest_mock -v --no-cov -m postgres tests test-int' || test $? -eq 137
    else
        echo "⚠️  No timeout command found, running without timeout..."
        BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run coverage run --source=basic_memory -m pytest -p pytest_mock -v --no-cov -m postgres tests test-int
    fi
    
    echo "🧩 Combining coverage data..."
    uv run coverage combine
    uv run coverage report -m
    uv run coverage html
    echo "Coverage report generated in htmlcov/index.html"

# Lint and fix code (calls fix)
lint: fix

# Lint and fix code
fix:
    uv run ruff check --fix --unsafe-fixes src tests test-int

# Type check code (ty)
typecheck:
    uv run ty check src tests test-int

# Type check code (pyright)
typecheck-pyright:
    uv run pyright

# Type check code (ty)
typecheck-ty:
    just typecheck

# Clean build artifacts and cache files
clean:
    find . -type f -name '*.pyc' -delete
    find . -type d -name '__pycache__' -exec rm -r {} +
    rm -rf installer/build/ installer/dist/ dist/
    rm -f rw.*.dmg .coverage.*

# Format code with ruff
format:
    uv run ruff format .

# Run MCP inspector tool
run-inspector:
    npx @modelcontextprotocol/inspector

# Run doctor checks in an isolated temp home/config
doctor:
    #!/usr/bin/env bash
    set -euo pipefail
    TMP_HOME=$(mktemp -d)
    TMP_CONFIG=$(mktemp -d)
    HOME="$TMP_HOME" \
    BASIC_MEMORY_ENV=test \
    BASIC_MEMORY_HOME="$TMP_HOME/basic-memory" \
    BASIC_MEMORY_CONFIG_DIR="$TMP_CONFIG" \
    ./.venv/bin/python -m basic_memory.cli.main doctor --local

# Run an isolated Logfire smoke workflow for local trace inspection
telemetry-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    TMP_HOME=$(mktemp -d)
    TMP_CONFIG=$(mktemp -d)
    TMP_PROJECT=$(mktemp -d)
    export HOME="$TMP_HOME"
    export BASIC_MEMORY_ENV="${BASIC_MEMORY_ENV:-dev}"
    export BASIC_MEMORY_HOME="$TMP_PROJECT/home-root"
    export BASIC_MEMORY_CONFIG_DIR="$TMP_CONFIG"
    export BASIC_MEMORY_NO_PROMOS=1
    export BASIC_MEMORY_LOG_LEVEL="${BASIC_MEMORY_LOG_LEVEL:-INFO}"
    export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED="${BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED:-false}"
    export BASIC_MEMORY_LOGFIRE_ENABLED="${BASIC_MEMORY_LOGFIRE_ENABLED:-true}"
    export BASIC_MEMORY_LOGFIRE_ENVIRONMENT="${BASIC_MEMORY_LOGFIRE_ENVIRONMENT:-telemetry-smoke}"
    if [[ -z "${BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE:-}" ]]; then
        if [[ -n "${LOGFIRE_TOKEN:-}" ]]; then
            export BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE=true
        else
            export BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE=false
        fi
    fi
    mkdir -p "$BASIC_MEMORY_HOME"
    echo "Telemetry smoke setup:"
    echo "  logfire_enabled=$BASIC_MEMORY_LOGFIRE_ENABLED"
    echo "  send_to_logfire=$BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE"
    echo "  log_level=$BASIC_MEMORY_LOG_LEVEL"
    echo "  semantic_search_enabled=$BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED"
    echo "  logfire_environment=$BASIC_MEMORY_LOGFIRE_ENVIRONMENT"
    echo "  project_path=$TMP_PROJECT"
    ./.venv/bin/python -m basic_memory.cli.main project add telemetry-smoke "$TMP_PROJECT" --default --local
    ./.venv/bin/python -m basic_memory.cli.main tool write-note --title "Telemetry Smoke" --folder notes --content "hello from smoke" --project telemetry-smoke --local
    ./.venv/bin/python -m basic_memory.cli.main tool read-note notes/telemetry-smoke --project telemetry-smoke --local
    ./.venv/bin/python -m basic_memory.cli.main tool edit-note notes/telemetry-smoke --operation append --content $'\n\nsmoke edit line' --project telemetry-smoke --local
    ./.venv/bin/python -m basic_memory.cli.main tool build-context notes/telemetry-smoke --project telemetry-smoke --local --page-size 5 --max-related 5
    ./.venv/bin/python -m basic_memory.cli.main tool search-notes telemetry --project telemetry-smoke --local
    ./.venv/bin/python -m basic_memory.cli.main doctor --local
    echo ""
    echo "Telemetry smoke complete."
    echo "Search Logfire for:"
    echo "  service_name: basic-memory-cli"
    echo "  environment: $BASIC_MEMORY_LOGFIRE_ENVIRONMENT"
    echo "  span names: mcp.tool.write_note, mcp.tool.read_note, mcp.tool.edit_note, mcp.tool.build_context, mcp.tool.search_notes, sync.project.run"


# Update all dependencies to latest versions
update-deps:
    uv sync --upgrade

# Run all code quality checks and tests
check: lint format typecheck test

# Run all code quality checks and all test suites, including semantic benchmarks
check-all: lint format typecheck test test-semantic

# Validate every consolidated agent package (Claude Code, Codex, skills, Hermes, OpenClaw)
package-check: package-check-claude-code package-check-codex package-check-skills package-check-hermes package-check-openclaw

# Alias for plugin/package validation during consolidation work
plugins-check: package-check

# Validate the host-native agent harnesses
agent-harness-check: package-check-claude-code package-check-hermes package-check-openclaw

# Claude Code plugin: manifests, bundled skills, bundled agent, and strict plugin validation
package-check-claude-code:
    just --justfile plugins/claude-code/justfile --working-directory plugins/claude-code check

# Codex plugin: manifest, bundled skills, hooks, MCP config, and schemas
package-check-codex:
    just --justfile plugins/codex/justfile --working-directory plugins/codex check

# Shared top-level SKILL.md source
package-check-skills:
    just --justfile skills/justfile --working-directory skills check

# Hermes plugin: native manifest plus hermetic unit test suite
package-check-hermes:
    just --justfile integrations/hermes/justfile --working-directory integrations/hermes check

# OpenClaw plugin: install deps, copy skills, typecheck, lint, build, test, and npm pack dry-run
package-check-openclaw:
    just --justfile integrations/openclaw/justfile --working-directory integrations/openclaw install
    just --justfile integrations/openclaw/justfile --working-directory integrations/openclaw release-check

# Generate Alembic migration with descriptive message
migration message:
    cd src/basic_memory/alembic && alembic revision --autogenerate -m "{{message}}"

# Set the Basic Memory version across release manifests (scope: all | core | packages)
set-version version scope="all":
    python3 scripts/update_versions.py "{{version}}" --scope "{{scope}}"

# Preview a version update without writing (scope: all | core | packages)
set-version-dry-run version scope="all":
    python3 scripts/update_versions.py "{{version}}" --scope "{{scope}}" --dry-run

# Set the version for just the plugin/agent artifacts (plugins, marketplaces, Hermes, OpenClaw)
set-packages-version version:
    just set-version "{{version}}" packages

# Preview a plugin/agent-artifact version update without writing
set-packages-version-dry-run version:
    just set-version-dry-run "{{version}}" packages

# Preview the consolidated manifest version update without changing files
release-dry-run version:
    just set-version-dry-run "{{version}}"

# Create a stable release (e.g., just release v0.13.2)
release version:
    #!/usr/bin/env bash
    set -euo pipefail
    
    # Validate version format
    if [[ ! "{{version}}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "❌ Invalid version format. Use: v0.13.2"
        exit 1
    fi
    
    # Extract version number without 'v' prefix
    VERSION_NUM=$(echo "{{version}}" | sed 's/^v//')
    
    echo "🚀 Creating stable release {{version}}"
    
    # Pre-flight checks
    echo "📋 Running pre-flight checks..."
    if [[ -n $(git status --porcelain) ]]; then
        echo "❌ Uncommitted changes found. Please commit or stash them first."
        exit 1
    fi
    
    if [[ $(git branch --show-current) != "main" ]]; then
        echo "❌ Not on main branch. Switch to main first."
        exit 1
    fi
    
    # Check if tag already exists
    if git tag -l "{{version}}" | grep -q "{{version}}"; then
        echo "❌ Tag {{version}} already exists"
        exit 1
    fi

    # Changelog must already be on main (land it via a normal PR first)
    if ! grep -q "^## {{version}} " CHANGELOG.md; then
        echo "❌ CHANGELOG.md has no entry for {{version}}. Land one via PR first."
        exit 1
    fi

    # Run quality checks
    echo "🔍 Running lint  checks..."
    just lint
    just typecheck

    # Update all package manifests to the one Basic Memory product version.
    echo "📝 Updating consolidated package versions..."
    just set-version "{{version}}"

    # Trigger: main's ruleset rejects direct pushes ("Changes must be made
    # through a pull request").
    # Why: the version bump must land on main before the tag is cut, so it
    # rides a release PR that is rebase-merged (the repo disallows merge
    # commits).
    # Outcome: the bump commit gets a new SHA on main; the tag is created on
    # that rebased commit, found by its commit subject.
    COMMIT_SUBJECT="chore: update version to $VERSION_NUM for {{version}} release"
    git checkout -b "release/{{version}}"
    git add \
        src/basic_memory/__init__.py \
        server.json \
        .claude-plugin/marketplace.json \
        plugins/claude-code/.claude-plugin/plugin.json \
        plugins/claude-code/.claude-plugin/marketplace.json \
        plugins/codex/.codex-plugin/plugin.json \
        integrations/hermes/plugin.yaml \
        integrations/hermes/__init__.py \
        integrations/openclaw/package.json
    git commit -s -m "$COMMIT_SUBJECT"

    echo "📤 Opening release PR..."
    git push -u origin "release/{{version}}"
    gh pr create --title "chore(core): release {{version}}" \
        --body "Version bump for {{version}}. See CHANGELOG.md for release notes."

    # Trigger: the PR may not be mergeable synchronously (merge gates,
    # required checks added later, or GitHub still computing mergeability).
    # Why: the tag must point at the bump commit on main, so the recipe
    # cannot tag until the merge has actually landed.
    # Outcome: try a direct rebase-merge, fall back to queueing auto-merge,
    # then poll main for the rebased bump commit before tagging.
    if ! gh pr merge "release/{{version}}" --rebase --delete-branch; then
        echo "⚠️  Direct merge did not complete (merge gates pending?). Queueing auto-merge..."
        gh pr merge "release/{{version}}" --rebase --delete-branch --auto
    fi

    echo "⏳ Waiting for the bump commit to land on main..."
    TAG_COMMIT=""
    for _ in $(seq 1 60); do
        git fetch origin main --quiet
        TAG_COMMIT=$(git log FETCH_HEAD --fixed-strings --grep "$COMMIT_SUBJECT" --format='%H' -1)
        [[ -n "$TAG_COMMIT" ]] && break
        sleep 5
    done
    if [[ -z "$TAG_COMMIT" ]]; then
        echo "❌ Bump commit not on main after 5 minutes (merge still pending?)."
        echo "   Once the release PR merges, finish the release manually:"
        echo "   git fetch origin main"
        echo "   git tag {{version}} \$(git log FETCH_HEAD --fixed-strings --grep \"$COMMIT_SUBJECT\" --format='%H' -1)"
        echo "   git push origin {{version}}"
        exit 1
    fi

    git checkout main
    git pull --ff-only origin main
    git branch -D "release/{{version}}" 2>/dev/null || true

    echo "🏷️  Creating tag {{version}} at $TAG_COMMIT..."
    git tag "{{version}}" "$TAG_COMMIT"
    git push origin "{{version}}"

    echo "✅ Release {{version}} created successfully!"
    echo "📦 GitHub Actions will build and publish to PyPI"
    echo "🔗 Monitor at: https://github.com/basicmachines-co/basic-memory/actions"
    echo ""
    echo "📝 REMINDER: Post-release tasks:"
    echo "   1. docs.basicmemory.com - Add a What's New page under content/2.whats-new/"
    echo "      and bump the badge in content/index.md (see that repo's CLAUDE.md)"
    echo "   2. basicmemory.com - No version number in the site UI; for a significant"
    echo "      release optionally add a post under src/content/blog/. Skip for patches."
    echo "   3. MCP Registry - Run: mcp-publisher publish"
    echo "   See: .claude/commands/release/release.md for detailed instructions"

# Create a beta release (e.g., just beta v0.13.2b1)
beta version:
    #!/usr/bin/env bash
    set -euo pipefail
    
    # Validate version format (allow beta/rc suffixes)
    if [[ ! "{{version}}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(b[0-9]+|rc[0-9]+)$ ]]; then
        echo "❌ Invalid beta version format. Use: v0.13.2b1 or v0.13.2rc1"
        exit 1
    fi
    
    # Extract version number without 'v' prefix
    VERSION_NUM=$(echo "{{version}}" | sed 's/^v//')
    
    echo "🧪 Creating beta release {{version}}"
    
    # Pre-flight checks
    echo "📋 Running pre-flight checks..."
    if [[ -n $(git status --porcelain) ]]; then
        echo "❌ Uncommitted changes found. Please commit or stash them first."
        exit 1
    fi
    
    if [[ $(git branch --show-current) != "main" ]]; then
        echo "❌ Not on main branch. Switch to main first."
        exit 1
    fi
    
    # Check if tag already exists
    if git tag -l "{{version}}" | grep -q "{{version}}"; then
        echo "❌ Tag {{version}} already exists"
        exit 1
    fi
    
    # Run quality checks
    echo "🔍 Running lint  checks..."
    just lint
    just typecheck
    
    # Update all package manifests to the one Basic Memory product version.
    echo "📝 Updating consolidated package versions..."
    just set-version "{{version}}"

    # Trigger: main's ruleset rejects direct pushes ("Changes must be made
    # through a pull request").
    # Why: the version bump must land on main before the tag is cut, so it
    # rides a release PR that is rebase-merged (the repo disallows merge
    # commits).
    # Outcome: the bump commit gets a new SHA on main; the tag is created on
    # that rebased commit, found by its commit subject.
    COMMIT_SUBJECT="chore: update version to $VERSION_NUM for {{version}} beta release"
    git checkout -b "release/{{version}}"
    git add \
        src/basic_memory/__init__.py \
        server.json \
        .claude-plugin/marketplace.json \
        plugins/claude-code/.claude-plugin/plugin.json \
        plugins/claude-code/.claude-plugin/marketplace.json \
        plugins/codex/.codex-plugin/plugin.json \
        integrations/hermes/plugin.yaml \
        integrations/hermes/__init__.py \
        integrations/openclaw/package.json
    git commit -s -m "$COMMIT_SUBJECT"

    echo "📤 Opening release PR..."
    git push -u origin "release/{{version}}"
    gh pr create --title "chore(core): release {{version}}" \
        --body "Version bump for {{version}} beta."

    # Trigger: the PR may not be mergeable synchronously (merge gates,
    # required checks added later, or GitHub still computing mergeability).
    # Why: the tag must point at the bump commit on main, so the recipe
    # cannot tag until the merge has actually landed.
    # Outcome: try a direct rebase-merge, fall back to queueing auto-merge,
    # then poll main for the rebased bump commit before tagging.
    if ! gh pr merge "release/{{version}}" --rebase --delete-branch; then
        echo "⚠️  Direct merge did not complete (merge gates pending?). Queueing auto-merge..."
        gh pr merge "release/{{version}}" --rebase --delete-branch --auto
    fi

    echo "⏳ Waiting for the bump commit to land on main..."
    TAG_COMMIT=""
    for _ in $(seq 1 60); do
        git fetch origin main --quiet
        TAG_COMMIT=$(git log FETCH_HEAD --fixed-strings --grep "$COMMIT_SUBJECT" --format='%H' -1)
        [[ -n "$TAG_COMMIT" ]] && break
        sleep 5
    done
    if [[ -z "$TAG_COMMIT" ]]; then
        echo "❌ Bump commit not on main after 5 minutes (merge still pending?)."
        echo "   Once the release PR merges, finish the release manually:"
        echo "   git fetch origin main"
        echo "   git tag {{version}} \$(git log FETCH_HEAD --fixed-strings --grep \"$COMMIT_SUBJECT\" --format='%H' -1)"
        echo "   git push origin {{version}}"
        exit 1
    fi

    git checkout main
    git pull --ff-only origin main
    git branch -D "release/{{version}}" 2>/dev/null || true

    echo "🏷️  Creating tag {{version}} at $TAG_COMMIT..."
    git tag "{{version}}" "$TAG_COMMIT"
    git push origin "{{version}}"

    echo "✅ Beta release {{version}} created successfully!"
    echo "📦 GitHub Actions will build and publish to PyPI as pre-release"
    echo "🔗 Monitor at: https://github.com/basicmachines-co/basic-memory/actions"
    echo "📥 Install with: uv tool install basic-memory --pre"
    echo ""
    echo "📝 REMINDER: For stable releases, update documentation sites:"
    echo "   1. docs.basicmemory.com - Add a What's New page under content/2.whats-new/"
    echo "      and bump the badge in content/index.md (see that repo's CLAUDE.md)"
    echo "   2. basicmemory.com - No version number in the site UI; for a significant"
    echo "      release optionally add a post under src/content/blog/. Skip for patches."
    echo "   See: .claude/commands/release/release.md for detailed instructions"

# List all available recipes
default:
    @just --list
