# Contributing to ctrlplane-enhanced

Thank you for your interest in contributing! This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.11+
- Git
- Docker (optional, for full-stack testing)

### Quick Start

`ash
# Clone the repository
git clone https://github.com/your-org/ctrlplane-enhanced.git
cd ctrlplane-enhanced

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install in development mode
pip install -e ".[dev]"

# Copy environment template
cp config/.env.example .env
# Edit .env with your API keys

# Run tests
pytest tests/test_agent.py -v

# Start the server
python -m agent.main serve
`

## Project Structure

| Directory | Purpose |
|-----------|---------|
| gent/ | Core agent: orchestrator, modules, memory |
| gent/modules/ | Risk scorer, auto-rollback, NL generator, resource scheduler |
| gent/memory/ | SQLite persistent memory manager |
| 	ools/ | LLM client, HuggingFace manager, knowledge updater |
| config/ | Runtime configuration (YAML + .env) |
| docker/ | Dockerfile + docker-compose |
| 	ests/ | Automated test suite |

## Coding Standards

### Style

- Follow PEP 8 with a line length of 100 characters
- Use type hints on all function signatures
- Write docstrings for all public classes and functions
- Use logging module — never print() in production code

### Linting

`ash
pip install ruff
ruff check . --ignore E501
`

### Type Checking

`ash
pip install mypy
mypy agent/ tools/ --ignore-missing-imports
`

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

`
feat: add deployment risk explanation endpoint
fix: resolve SQLite locking in memory_manager under concurrent access
docs: update API reference for v1.1
test: add integration test for auto-rollback flow
chore: update dependencies
`

## Pull Request Process

1. **Fork** the repository and create a feature branch from main
2. **Write tests** for any new functionality — aim for > 80% coverage
3. **Run the full test suite**: pytest tests/test_agent.py -v
4. **Lint your code**: uff check . --ignore E501
5. **Update documentation** if you changed behavior or added features
6. **Open a PR** with a clear description of changes and motivation

### PR Checklist

- [ ] All tests pass
- [ ] New code has type hints
- [ ] New code has docstrings
- [ ] No secrets or API keys committed
- [ ] equirements.txt updated if new dependencies added

## Adding a New Module

1. Create gent/modules/your_module.py implementing your feature
2. Add a lazy accessor in gent/orchestrator.py
3. Add a new CLI subcommand in gent/main.py
4. Add a new FastAPI endpoint in gent/main.py
5. Add tests in 	ests/test_agent.py
6. Update CLAUDE.md and PROJECT-detail.md

## Reporting Bugs

Use the [Bug Report template](/.github/ISSUE_TEMPLATE/bug_report.md). Include:
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, ctrlplane-enhanced version)

## License

By contributing, you agree that your contributions will be licensed under [Apache-2.0](./LICENSE).
