<!-- Thanks for contributing! Please fill in each section. -->

## Summary

<!-- What does this PR change, and why? One or two sentences. -->

## Related issue

<!-- Fixes #123, Closes #456, or "N/A". -->

## Type of change

- [ ] Bug fix (non-breaking change fixing an issue)
- [ ] New feature (non-breaking change adding functionality)
- [ ] Breaking change (existing behaviour changes)
- [ ] Documentation or tooling
- [ ] Security fix (coordinate disclosure first — see SECURITY.md)

## Test plan

<!-- How did you verify this? Unit tests? Manual? -->

- [ ] `ruff check .`
- [ ] `black --check .`
- [ ] `pytest --cov=src`
- [ ] `bandit -r src -c pyproject.toml`

## Backwards compatibility

<!-- Does this change existing config keys, CLI flags, manifest schema, or
     on-disk layout? If yes, describe the migration path. -->

## CHANGELOG

- [ ] Entry added under `[Unreleased]` in `CHANGELOG.md`.

## Checklist

- [ ] Commits follow Conventional Commits (`feat(...)`, `fix(...)`, …).
- [ ] Type hints added/updated on public functions.
- [ ] Docstrings added/updated.
- [ ] No secrets, credentials, or real webhook URLs in diff.
- [ ] No new `except Exception:` without a reason comment.
