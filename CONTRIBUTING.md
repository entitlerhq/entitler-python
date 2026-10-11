# Contributing

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync
```

`uv sync` creates `.venv` with the package, the `snapshots` extra's `cryptography` and the
development tools, from the committed `uv.lock`.

## Checks

Run these before pushing; CI runs the same commands, and its `All checks passed` job is the one
required check.

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pyright
uv run coverage run -m pytest && uv run coverage report
uv run python -W error -m pdoc --docformat google -o site entitler entitler.testing entitler.cli
uv run python scripts/snippets.py
uv run python scripts/examples.py check
uv run pytest examples/testing
uv build && uv run twine check --strict dist/*
```

- Unit tests (`tests/unit`) make no request. They include the shared conformance suite in
  `tests/fixtures/conformance`, which is copied from the SDK specification unchanged.
- Coverage must stay at 90% or more.
- `scripts/snippets.py` type-checks every Python snippet in the README and `docs/`.
- `scripts/examples.py check` type-checks each example folder; `scripts/examples.py run` runs them
  against your environment, with a secret test key in `ENTITLER_KEY`.

### Live API tests

`tests/integration` runs against the live API in the SDK's test environment, with its secret key:

```sh
ENTITLER_TEST_KEY=ent_test_... uv run pytest tests/integration
```

Tests that need the publishable key run when `ENTITLER_TEST_PUBLISHABLE_KEY` is set, and report as
skipped otherwise. Never print or commit either key. In CI, `ENTITLER_TEST_KEY` is a repository
secret and `ENTITLER_TEST_PUBLISHABLE_KEY` a repository variable; pull requests from forks and from
Dependabot have no access to the secret, so their live tests are skipped.

## Style

- Australian English in all prose, sentence case, no emoji.
- Every public type, function, method, property and constant has a docstring (Google style), which
  becomes the API reference. No other comments: rename, extract or restructure instead. Lint
  suppressions carry a reason.
- Python conventions decide every name and shape.

## Releasing

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new `## [x.y.z] - YYYY-MM-DD` heading
   and update the links at the bottom.
2. Set `__version__` in `src/entitler/_version.py`, the version's one source, and merge.
3. Tag the merge commit `vx.y.z` and push the tag. `publish.yml` checks that the tag matches the
   version, runs CI, builds, publishes to PyPI through trusted publishing in the `release`
   environment, and creates the GitHub release from the changelog section.
