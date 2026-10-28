# Contributing

- Work on a branch; the PR title is a conventional commit, `type(scope): subject`, for example
  `fix(loamz-server): reject expired vending tokens`. Scopes are listed in `.github/workflows/pr-title.yml`.
- Use `uv` for Python, never `pip`.

```bash
make sync     # uv (Python 3.14)
make check    # lint, import contracts, fast unit tests

# one package
cd server/engine && uv run --package loamz-engine pytest -q -n 4
cd server/server && uv run --package loamz-server pytest -q -n 4
cd server/reader && uv run pytest -q          # Python 3.13, own lock
```

- Tests that need Docker are marked `slow` or `integration` and run on merge-queue runs, or on a PR labelled
  `run-integration` (`make test-slow`).
- The import contracts in `.importlinter` are enforced in CI (`make imports`).
- The client libraries (`loamz-core`, `loamz-identity`, `loamz-data`, `loamz-agents`) live in
  [loamz/loamz-python](https://github.com/loamz/loamz-python); change them there.
- Nothing is deployed yet, so there is no backward-compatibility burden: delete old code instead of shimming.
