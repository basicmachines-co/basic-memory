# Basic Memory - Modern Command Runner

# Install dependencies
install:
    uv pip install -e ".[dev]"
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
    uv run pytest -p pytest_mock -v --no-cov tests

# Run unit tests against Postgres
test-unit-postgres:
    BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov tests

# Run integration tests against SQLite
test-int-sqlite:
    uv run pytest -p pytest_mock -v --no-cov test-int

# Run integration tests against Postgres
# Note: Uses timeout due to FastMCP Client + asyncpg cleanup hang (tests pass, process hangs on exit)
# See: https://github.com/jlowin/fastmcp/issues/1311
test-int-postgres:
    timeout --signal=KILL 600 bash -c 'BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest -p pytest_mock -v --no-cov test-int' || test $? -eq 137

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
    uv run pytest -p pytest_mock -v --no-cov -m windows tests test-int

# Run benchmark tests only (performance testing)
# These are slow tests that measure sync performance with various file counts
# Excluded from default test runs to keep CI fast
test-benchmark:
    uv run pytest -p pytest_mock -v --no-cov -m benchmark tests test-int

# Run all tests including Windows, Postgres, and Benchmarks (for CI/comprehensive testing)
# Use this before releasing to ensure everything works across all backends and platforms
test-all:
    uv run pytest -p pytest_mock -v --no-cov tests test-int

# Generate HTML coverage report
coverage:
    uv run pytest -p pytest_mock -v -n auto tests test-int --cov-report=html
    @echo "Coverage report generated in htmlcov/index.html"

# Lint and fix code (calls fix)
lint: fix

# Lint and fix code
fix:
    uv run ruff check --fix --unsafe-fixes src tests test-int

# Type check code
typecheck:
    uv run pyright

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

# Build macOS installer
installer-mac:
    cd installer && chmod +x make_icons.sh && ./make_icons.sh
    cd installer && uv run python setup.py bdist_mac

# Build Windows installer
installer-win:
    cd installer && uv run python setup.py bdist_win32

# Update all dependencies to latest versions
update-deps:
    uv sync --upgrade

# Run all code quality checks and tests
check: lint format typecheck test

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
    echo "🔍 Running quality checks..."
    just check
    
    # Update version in __init__.py
    echo "📝 Updating version in __init__.py..."
    sed -i.bak "s/__version__ = \".*\"/__version__ = \"$VERSION_NUM\"/" src/basic_memory/__init__.py
    rm -f src/basic_memory/__init__.py.bak
    
    # Commit version update
    git add src/basic_memory/__init__.py
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
    echo "🔍 Running quality checks..."
    just check
    
    # Update version in __init__.py
    echo "📝 Updating version in __init__.py..."
    sed -i.bak "s/__version__ = \".*\"/__version__ = \"$VERSION_NUM\"/" src/basic_memory/__init__.py
    rm -f src/basic_memory/__init__.py.bak
    
    # Commit version update
    git add src/basic_memory/__init__.py
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

# List all available recipes
default:
    @just --list
