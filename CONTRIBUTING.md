# Contributing to backup-handler

Thanks for your interest in improving backup-handler. This document explains
how to set up a development environment, our coding standards, and the pull
request workflow.

## Code of Conduct

This project follows the [Contributor Covenant](./CODE_OF_CONDUCT.md). By
participating, you agree to uphold its terms.

## Reporting a vulnerability

Do **not** open public issues for security problems. See
[SECURITY.md](./SECURITY.md) for the private disclosure process.

## Development environment

Requires Python **3.10+**.

```bash
# Clone
git clone https://github.com/SP1R4/BackupHandler.git
cd BackupHandler

# Virtualenv
python -m venv venv
source venv/bin/activate

# Editable install with dev + test extras
pip install -e ".[dev,test,security]"

# Install the pre-commit hooks
pre-commit install
pre-commit install --hook-type commit-msg
```

## Coding standards

- **Formatting**: `black` (line length 110) and `ruff format`.
- **Linting**: `ruff check` — all rules in `[tool.ruff.lint]` must pass.
- **Typing**: public functions must have type hints; `mypy src` should pass
  (or at least not regress) on touched modules.
- **Docstrings**: Google style, with an opening one-line summary and a
  blank line before `Parameters:` / `Returns:` sections.
- **Exceptions**: catch narrow, specific classes. Never `except Exception:`
  unless re-raising or at a clearly documented top-level boundary.
- **Security**: prefer `secrets` over `random`, validate external input,
  never log resolved secret values.

Run everything locally before pushing:

```bash
ruff check .
ruff format --check .
black --check .
mypy src
pytest --cov=src
bandit -r src -c pyproject.toml
```

## Commit messages

Follow the Conventional Commits convention:

```
<type>(<scope>): <summary>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`.

Example:

```
feat(encryption): add parallel worker pool for decrypt_directory

Wraps the decrypt loop with ThreadPoolExecutor when `workers > 1` so large
restores no longer serialize on CPU-bound AES-GCM verification.

Closes #42
```

## Branching and pull requests

1. Branch from `main`: `git checkout -b feat/short-description`.
2. Make focused commits — one logical change per commit.
3. Keep PRs under ~400 changed lines where possible; split larger work.
4. Ensure CI passes locally before pushing.
5. Reference any related issue in the PR description.
6. At least one approving review is required for merge.
7. PRs are merged via **squash-merge** using the Conventional Commits
   subject as the merge message.

## Tests

All new features need tests. Update `tests/` alongside the code change.

- Unit tests go under `tests/` (fast, no external services).
- Integration tests go under `tests/integration/` and are marked with
  `@pytest.mark.integration`. They are excluded from default runs; run
  them explicitly with `pytest -m integration`.
- Aim for meaningful coverage of the code you touch; keep overall coverage
  ≥ 80%.

## Release process

Maintainers only.

1. Update `src/__version__.py`.
2. Move `[Unreleased]` entries in `CHANGELOG.md` into a new versioned
   section with today's date.
3. Commit: `chore(release): v<x.y.z>`.
4. Tag: `git tag -s v<x.y.z> -m "v<x.y.z>"` and push tag.
5. The `release.yml` workflow builds and publishes the artefacts.
