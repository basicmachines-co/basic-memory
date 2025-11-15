# Dual-Backend Testing

Basic Memory tests run against both SQLite and Postgres backends to ensure compatibility.

## Quick Start

```bash
# Run tests against SQLite only (default, no setup needed)
pytest

# Run tests against Postgres only (requires docker-compose)
docker-compose -f docker-compose-postgres.yml up -d
pytest -m postgres

# Run tests against BOTH backends
docker-compose -f docker-compose-postgres.yml up -d
pytest --run-all-backends  # Not yet implemented - run both commands above
```

## How It Works

### Parametrized Backend Fixture

The `db_backend` fixture is parametrized to run tests against both `sqlite` and `postgres`:

```python
@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def db_backend(request) -> Literal["sqlite", "postgres"]:
    return request.param
```

### Backend-Specific Engine Factories

Each backend has its own engine factory implementation:

- **`sqlite_engine_factory`** - Uses in-memory SQLite (fast, isolated)
- **`postgres_engine_factory`** - Uses Postgres test database (realistic, requires Docker)

The main `engine_factory` fixture delegates to the appropriate implementation based on `db_backend`.

### Configuration

The `app_config` fixture automatically configures the correct backend:

```python
# SQLite config
database_backend = DatabaseBackend.SQLITE
database_url = None  # Uses default SQLite path

# Postgres config
database_backend = DatabaseBackend.POSTGRES
database_url = "postgresql+asyncpg://basic_memory_user:dev_password@localhost:5433/basic_memory_test"
```

## Running Postgres Tests

### 1. Start Postgres Docker Container

```bash
docker-compose -f docker-compose-postgres.yml up -d
```

This starts:
- Postgres 17 on port **5433** (not 5432 to avoid conflicts)
- Test database: `basic_memory_test`
- Credentials: `basic_memory_user` / `dev_password`

### 2. Run Postgres Tests

```bash
# Run only Postgres tests
pytest -m postgres

# Run specific test with Postgres
pytest tests/test_entity_repository.py::test_create -m postgres

# Skip Postgres tests (default behavior)
pytest -m "not postgres"
```

### 3. Stop Docker Container

```bash
docker-compose -f docker-compose-postgres.yml down
```

## Test Isolation

### SQLite Tests
- Each test gets a fresh in-memory database
- Automatic cleanup (database destroyed after test)
- No setup required

### Postgres Tests
- Database is **cleaned before each test** (drop all tables, recreate)
- Tests share the same Postgres instance but get isolated schemas
- Requires Docker Compose to be running

## Markers

- `postgres` - Marks tests that run against Postgres backend
- Use `-m postgres` to run only Postgres tests
- Use `-m "not postgres"` to skip Postgres tests (default)

## CI Integration

### GitHub Actions

Use service containers for Postgres (no Docker Compose needed):

```yaml
jobs:
  test:
    runs-on: ubuntu-latest

    # Postgres service container
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_DB: basic_memory_test
          POSTGRES_USER: basic_memory_user
          POSTGRES_PASSWORD: dev_password
        ports:
          - 5433:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - name: Run SQLite tests
        run: pytest -m "not postgres"

      - name: Run Postgres tests
        run: pytest -m postgres
```

## Troubleshooting

### Postgres tests fail with "connection refused"

Make sure Docker Compose is running:
```bash
docker-compose -f docker-compose-postgres.yml ps
docker-compose -f docker-compose-postgres.yml logs postgres
```

### Port 5433 already in use

Either:
- Stop the conflicting service
- Change the port in `docker-compose-postgres.yml` and `tests/conftest.py`

### Tests hang or timeout

Check Postgres health:
```bash
docker-compose -f docker-compose-postgres.yml exec postgres pg_isready -U basic_memory_user
```

## Future Enhancements

- [ ] Add `--run-all-backends` CLI flag to run both backends in sequence
- [ ] Implement test fixtures for backend-specific features (e.g., Postgres full-text search vs SQLite FTS5)
- [ ] Add performance comparison benchmarks between backends