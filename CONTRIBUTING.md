# Contributing to Basic Memory

Thank you for considering contributing to Basic Memory! This document outlines the process for contributing to the
project and how to get started as a developer.

## Getting Started

### Development Environment

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/basicmachines-co/basic-memory.git
   cd basic-memory
   ```

2. **Install Dependencies**:
   ```bash
   # Using make (recommended)
   make install
   
   # Or using uv
   uv install -e ".[dev]"
   
   # Or using pip
   pip install -e ".[dev]"
   ```

3. **Run the Tests**:
   ```bash
   # Run all tests
   make test
   # or
   uv run pytest -p pytest_mock -v
   
   # Run a specific test
   pytest tests/path/to/test_file.py::test_function_name
   ```

### Development Workflow

1. **Fork the Repo**: Fork the repository on GitHub and clone your copy.
2. **Create a Branch**: Create a new branch for your feature or fix.
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-you-are-fixing
   ```
3. **Make Your Changes**: Implement your changes with appropriate test coverage.
4. **Check Code Quality**:
   ```bash
   # Run all checks at once
   make check
   
   # Or run individual checks
   make lint      # Run linting
   make format    # Format code
   make type-check  # Type checking
   ```
5. **Test Your Changes**: Ensure all tests pass locally and maintain 100% test coverage.
   ```bash
   make test
   ```
6. **Submit a PR**: Submit a pull request with a detailed description of your changes.

## LLM-Assisted Development

This project is designed for collaborative development between humans and LLMs (Large Language Models):

1. **CLAUDE.md**: The repository includes a `CLAUDE.md` file that serves as a project guide for both humans and LLMs.
   This file contains:
    - Key project information and architectural overview
    - Development commands and workflows
    - Code style guidelines
    - Documentation standards

2. **AI-Human Collaborative Workflow**:
    - We encourage using LLMs like Claude for code generation, reviews, and documentation
    - When possible, save context in markdown files that can be referenced later
    - This enables seamless knowledge transfer between different development sessions
    - Claude can help with implementation details while you focus on architecture and design

3. **Adding to CLAUDE.md**:
    - If you discover useful project information or common commands, consider adding them to CLAUDE.md
    - This helps all contributors (human and AI) maintain consistent knowledge of the project

## Pull Request Process

1. **Create a Pull Request**: Open a PR against the `main` branch with a clear title and description.
2. **Sign the Developer Certificate of Origin (DCO)**: All contributions require signing our DCO, which certifies that
   you have the right to submit your contributions. This will be automatically checked by our CLA assistant when you
   create a PR.
3. **PR Description**: Include:
    - What the PR changes
    - Why the change is needed
    - How you tested the changes
    - Any related issues (use "Fixes #123" to automatically close issues)
4. **Code Review**: Wait for code review and address any feedback.
5. **CI Checks**: Ensure all CI checks pass.
6. **Merge**: Once approved, a maintainer will merge your PR.

## Developer Certificate of Origin

By contributing to this project, you agree to the [Developer Certificate of Origin (DCO)](CLA.md). This means you
certify that:

- You have the right to submit your contributions
- You're not knowingly submitting code with patent or copyright issues
- Your contributions are provided under the project's license (AGPL-3.0)

This is a lightweight alternative to a Contributor License Agreement and helps ensure that all contributions can be
properly incorporated into the project and potentially used in commercial applications.

### Signing Your Commits

Sign your commit:

**Using the `-s` or `--signoff` flag**:

```bash
git commit -s -m "Your commit message"
```

This adds a `Signed-off-by` line to your commit message, certifying that you adhere to the DCO.

The sign-off certifies that you have the right to submit your contribution under the project's license and verifies your
agreement to the DCO.

## Code Style Guidelines

- **Python Version**: Python 3.12+ with full type annotations
- **Line Length**: 100 characters maximum
- **Formatting**: Use ruff for consistent styling
- **Import Order**: Standard lib, third-party, local imports
- **Naming**: Use snake_case for functions/variables, PascalCase for classes
- **Documentation**: Add docstrings to public functions, classes, and methods
- **Type Annotations**: Use type hints for all functions and methods

## Testing Guidelines

- **Coverage Target**: We aim for 100% test coverage for all code
- **Test Framework**: Use pytest for unit and integration tests
- **Mocking**: Use pytest-mock for mocking dependencies only when necessary
- **Edge Cases**: Test both normal operation and edge cases
- **Database Testing**: Use in-memory SQLite for testing database operations
- **Fixtures**: Use async pytest fixtures for setup and teardown

## Release Process

Basic Memory uses automatic versioning based on git tags with `uv-dynamic-versioning`. Here's how releases work:

### Version Management
- **Development versions**: Automatically generated from git commits (e.g., `0.12.4.dev26+468a22f`)
- **Beta releases**: Created by tagging with beta suffixes (e.g., `git tag v0.13.0b1`)
- **Stable releases**: Created by tagging with version numbers (e.g., `git tag v0.13.0`)

### Release Workflows

#### Development Builds
- Automatically published to PyPI on every commit to `main`
- Version format: `0.12.4.dev26+468a22f` (base version + dev + commit count + hash)
- Users install with: `pip install basic-memory --pre --force-reinstall`

#### Beta Releases
1. Create and push a beta tag: `git tag v0.13.0b1 && git push origin v0.13.0b1`
2. GitHub Actions automatically builds and publishes to PyPI
3. Users install with: `pip install basic-memory --pre`

#### Stable Releases
1. Create and push a version tag: `git tag v0.13.0 && git push origin v0.13.0`
2. GitHub Actions automatically:
   - Builds the package with version `0.13.0`
   - Creates GitHub release with auto-generated notes
   - Publishes to PyPI
3. Users install with: `pip install basic-memory`

### For Contributors
- No manual version bumping required
- Versions are automatically derived from git tags
- Focus on code changes, not version management

## Creating Issues

If you're planning to work on something, please create an issue first to discuss the approach. Include:

- A clear title and description
- Steps to reproduce if reporting a bug
- Expected behavior vs. actual behavior
- Any relevant logs or screenshots
- Your proposed solution, if you have one

## Code of Conduct

All contributors must follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Thank You!

Your contributions help make Basic Memory better. We appreciate your time and effort!