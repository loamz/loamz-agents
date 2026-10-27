# Loamz Agents

The agent runtime and its hosting: kernel, toolkit, providers, stores, evals (`server/runtime`, package `loamz.runtime`),
the HTTP API, console, channels and automation (`server/agents`, package `loamz.server.agents`), the agents console
(`apps/agents-console`) and the API contract (`specs/agents`).

Built on the storage engines of [loamz-data](https://github.com/loamz/loamz-data) (`loamz-engine`) and the token verifier
of [loamz-python](https://github.com/loamz/loamz-python) (`loamz-identity[server]`). It reaches Loamz Data only through the
client libraries. Split out of `loamz-server` with its history kept; see `SPLIT.md`.

    uv sync --all-packages --all-extras --all-groups
    make test        # runtime and agents-server tests
    uv run lint-imports --config .importlinter
