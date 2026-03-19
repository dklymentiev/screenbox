# Contributing to Screenbox

Thank you for your interest in Screenbox! This guide covers how to set up a development environment, run tests, and submit changes.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/dklymentiev/screenbox.git
cd screenbox

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"

# Verify installation
python3 -m pytest tests/ -q
```

## Project Structure

```
screenbox/
  src/screenbox/           # Main package
    desktop/               # Desktop interaction (input, OCR, grid, a11y)
    tools/                 # MCP tool handlers (one file per tool)
    manager.py             # Desktop lifecycle (create, destroy, snapshot)
    browser.py             # Chrome DevTools Protocol client
    globals.py             # MCP server instance and shared state
    config.py              # Configuration loading
    knowledge.py           # App knowledge base
    vision.py              # Vision model integration
  tests/                   # Unit and integration tests
  docker/                  # Dockerfiles and entrypoint scripts
  dashboard/               # Web dashboard
```

## Running Tests

```bash
# Unit tests only (fast, no Docker needed)
PYTHONPATH=src python3 -m pytest tests/ -q \
  --ignore=tests/test_filesystem_api.py \
  --ignore=tests/test_desktop_smoke.py \
  --ignore=tests/test_integration.py

# Full suite including integration tests (requires Docker)
PYTHONPATH=src python3 -m pytest tests/ -q

# Single test file
PYTHONPATH=src python3 -m pytest tests/test_mcp_tools.py -v
```

## Code Style

- **Linter:** ruff (config in pyproject.toml)
- **Line length:** 120 characters
- **Type hints:** Required on all public methods
- **Docstrings:** Google style on all public methods

```bash
# Run linter
ruff check src/

# Auto-fix
ruff check src/ --fix
```

## Making Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes
4. Run tests: `python3 -m pytest tests/ -q`
5. Run linter: `ruff check src/`
6. Commit with a descriptive message (see below)
7. Open a pull request

## Commit Messages

Format: `type: short description`

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

Examples:
- `feat: add PDF export to desktop_chrome`
- `fix: handle null params in desktop_manage`
- `docs: update MCP tool reference`

## MCP Tool Guidelines

Each MCP tool lives in `src/screenbox/tools/`. When adding or modifying tools:

- Use `Optional[str] = None` for optional string parameters (MCP clients send null)
- Always validate `desktop_id` through `get_desktop()` (handles state checks)
- Log actions with `log_action()` for replay support
- Return JSON strings from tool handlers
- Add tests in `tests/test_mcp_tools.py`

## Docker Images

Build desktop images locally:

```bash
# XFCE (lightweight, ~920MB)
docker build -f docker/Dockerfile -t screenbox:latest docker/

# MATE (full desktop)
docker build -f docker/Dockerfile.mate -t screenbox:mate docker/
```

## Developer Certificate of Origin

All contributions must include a `Signed-off-by` line in the commit message,
certifying the [Developer Certificate of Origin v1.1](https://developercertificate.org/).

Use `git commit -s` to add this automatically.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
