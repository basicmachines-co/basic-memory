# Basic Memory - Modern Command Runner

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
test-unit-sqlite:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov tests

# Run unit tests against Postgres
test-unit-postgres:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov tests

# Run integration tests against SQLite (excludes semantic benchmarks — use just test-semantic)
test-int-sqlite:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m "not semantic" test-int

# Run integration tests against Postgres
# Note: Uses timeout due to FastMCP Client + asyncpg cleanup hang (tests pass, process hangs on exit)
# See: https://github.com/jlowin/fastmcp/issues/1311
test-int-postgres:
    #!/usr/bin/env bash
    set -euo pipefail
    # Use gtimeout (macOS/Homebrew) or timeout (Linux)
    TIMEOUT_CMD=$(command -v gtimeout || command -v timeout || echo "")
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD --signal=KILL 600 bash -c 'BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov -m "not semantic" test-int' || test $? -eq 137
    else
        echo "⚠️  No timeout command found, running without timeout..."
        BASIC_MEMORY_ENV=test BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov -m "not semantic" test-int
    fi

# Run tests impacted by recent changes (requires pytest-testmon)
testmon *args:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov --testmon --testmon-forceselect {{args}}

# Run MCP smoke test (fast end-to-end loop)
test-smoke:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m smoke test-int/mcp/test_smoke_integration.py

# Run graph intelligence API contract tests only
test-graph-intel-api:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov tests/api/v2/test_graph_intelligence_router.py

# Run graph intelligence MCP tests only
test-graph-intel-mcp:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov tests/mcp/clients/test_graph_clients.py tests/mcp/test_tool_graph_intelligence.py tests/mcp/test_tool_contracts.py

# Run graph intelligence CLI passthrough tests only
test-graph-intel-cli:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov tests/cli/test_cli_tool_graph_intelligence_json_output.py

# Run the full graph intelligence fast iteration slice
test-graph-intel:
    just test-graph-intel-api
    just test-graph-intel-mcp
    just test-graph-intel-cli

# Fast local loop: lint, format, typecheck, impacted tests
fast-check:
    just fix
    just format
    just typecheck
    just testmon
    just test-smoke

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
test-windows:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m windows tests test-int

# Run benchmark tests only (performance testing)
# These are slow tests that measure sync performance with various file counts
# Excluded from default test runs to keep CI fast
test-benchmark:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m benchmark tests test-int

# Run semantic search quality benchmarks (all combos)
test-semantic:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m semantic test-int/semantic/

# Run semantic benchmarks with JSON artifact output, then show report
test-semantic-report:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_BENCHMARK_OUTPUT=.benchmarks/semantic-quality.jsonl uv run pytest -p pytest_mock -v -s --no-cov -m semantic test-int/semantic/
    uv run python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl

# Run semantic benchmarks (Postgres combos only)
test-semantic-postgres:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov -m semantic -k postgres test-int/semantic/

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
test-all:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov tests test-int

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

# Type check code (pyright)
typecheck:
    uv run pyright

# Type check code (ty)
typecheck-ty:
    uv run ty check src/

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


# Update all dependencies to latest versions
update-deps:
    uv sync --upgrade

# Run all code quality checks and tests
check: lint format typecheck test

# Run all code quality checks and all test suites, including semantic benchmarks
check-all: lint format typecheck test test-semantic

# Generate Alembic migration with descriptive message
migration message:
    cd src/basic_memory/alembic && alembic revision --autogenerate -m "{{message}}"

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
    
    # Run quality checks
    echo "🔍 Running lint  checks..."
    just lint
    just typecheck
    
    # Update version in __init__.py
    echo "📝 Updating version in __init__.py..."
    sed -i.bak "s/__version__ = \".*\"/__version__ = \"$VERSION_NUM\"/" src/basic_memory/__init__.py
    rm -f src/basic_memory/__init__.py.bak

    # Update version in server.json (MCP registry metadata)
    echo "📝 Updating version in server.json..."
    sed -i.bak "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION_NUM\"/g" server.json
    rm -f server.json.bak

    # Commit version update
    git add src/basic_memory/__init__.py server.json
    git commit -m "chore: update version to $VERSION_NUM for {{version}} release"
    
    # Create and push tag
    echo "🏷️  Creating tag {{version}}..."
    git tag "{{version}}"
    
    echo "📤 Pushing to GitHub..."
    git push origin main
    git push origin "{{version}}"
    
    echo "✅ Release {{version}} created successfully!"
    echo "📦 GitHub Actions will build and publish to PyPI"
    echo "🔗 Monitor at: https://github.com/basicmachines-co/basic-memory/actions"
    echo ""
    echo "📝 REMINDER: Post-release tasks:"
    echo "   1. docs.basicmemory.com - Add release notes to src/pages/latest-releases.mdx"
    echo "   2. basicmachines.co - Update version in src/components/sections/hero.tsx"
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
    
    # Update version in __init__.py
    echo "📝 Updating version in __init__.py..."
    sed -i.bak "s/__version__ = \".*\"/__version__ = \"$VERSION_NUM\"/" src/basic_memory/__init__.py
    rm -f src/basic_memory/__init__.py.bak

    # Update version in server.json (MCP registry metadata)
    echo "📝 Updating version in server.json..."
    sed -i.bak "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION_NUM\"/g" server.json
    rm -f server.json.bak

    # Commit version update
    git add src/basic_memory/__init__.py server.json
    git commit -m "chore: update version to $VERSION_NUM for {{version}} beta release"
    
    # Create and push tag
    echo "🏷️  Creating tag {{version}}..."
    git tag "{{version}}"
    
    echo "📤 Pushing to GitHub..."
    git push origin main
    git push origin "{{version}}"
    
    echo "✅ Beta release {{version}} created successfully!"
    echo "📦 GitHub Actions will build and publish to PyPI as pre-release"
    echo "🔗 Monitor at: https://github.com/basicmachines-co/basic-memory/actions"
    echo "📥 Install with: uv tool install basic-memory --pre"
    echo ""
    echo "📝 REMINDER: For stable releases, update documentation sites:"
    echo "   1. docs.basicmemory.com - Add release notes to src/pages/latest-releases.mdx"
    echo "   2. basicmachines.co - Update version in src/components/sections/hero.tsx"
    echo "   See: .claude/commands/release/release.md for detailed instructions"

# List all available recipes
default:
    @just --list
