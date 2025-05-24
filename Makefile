.PHONY: install test test-module lint clean format type-check installer-mac installer-win check

install:
	pip install -e ".[dev]"

test:
	uv run pytest -p pytest_mock -v

# Run tests for a specific module
# Usage: make test-module m=path/to/module.py [cov=module_path]
test-module:
	@if [ -z "$(m)" ]; then \
		echo "Usage: make test-module m=path/to/module.py [cov=module_path]"; \
		exit 1; \
	fi; \
	if [ -z "$(cov)" ]; then \
		uv run pytest $(m) -v; \
	else \
		uv run pytest $(m) -v --cov=$(cov); \
	fi

lint:
	ruff check . --fix

type-check:
	uv run pyright

clean:
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -r {} +
	rm -rf installer/build/
	rm -rf installer/dist/
	rm -f rw.*.dmg
	rm -rf dist
	rm -rf installer/build
	rm -rf installer/dist
	rm -f .coverage.*

format:
	uv run ruff format .

# run inspector tool
run-inspector:
	npx @modelcontextprotocol/inspector

# Build app installer
installer-mac:
	cd installer && chmod +x make_icons.sh && ./make_icons.sh
	cd installer && uv run python setup.py bdist_mac

installer-win:
	cd installer && uv run python setup.py bdist_win32


update-deps:
	uv lock --upgrade

check: lint  format type-check test


# Target for generating Alembic migrations with a message from command line
migration:
	@if [ -z "$(m)" ]; then \
		echo "Usage: make migration m=\"Your migration message\""; \
		exit 1; \
	fi; \
	cd src/basic_memory/alembic && alembic revision --autogenerate -m "$(m)"