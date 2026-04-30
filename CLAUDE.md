# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Python 3.12 FastAPI service that **consumes** upstream Model Context Protocol (MCP) servers — it is not itself an MCP server. At startup it reads a JSON registry of upstream servers (`ANDELA_MCP_SERVERS_CONFIG_PATH`), opens a long-lived `ClientSession` to each via stdio / streamable-HTTP / SSE, and re-exposes their tools through a small HTTP API (`/v1/tools`, `/v1/tools/call`).

## Common commands

Use the `Makefile` — it pins the canonical invocations.

| Action | Command |
| --- | --- |
| Install deps + hooks | `make install` |
| Lint + typecheck + test | `make check` |
| Auto-fix style | `make fmt` |
| Run service locally | `make run` |
| Single test | `uv run pytest tests/unit/test_client.py::test_load_server_configs_parses_entries` |
| Integration tests | `make test-integration` (skipped by default; opt-in via `pytest -m integration`) |
| Build + run container | `make docker-run` |
| Terraform plan | `make tf-plan ENV=dev` |

`pyproject.toml` enforces `--cov-fail-under=80` — tests fail if coverage drops below that.

## Architecture (the parts that span files)

- **Settings boundary — `src/andela_mcp/config.py`.** All env-derived config goes through `Settings` (pydantic-settings, prefix `ANDELA_MCP_`, nested delimiter `__`). Don't read `os.environ` from anywhere else. `MCPServerConfig` validates per-transport requirements (stdio needs `command`; http/sse need `url`) in `model_validator`.
- **Lifespan owns MCP sessions — `src/andela_mcp/server.py`.** `create_app()` registers an async `lifespan` that loads the server registry, calls `MCPClient.connect()` for each, and stashes them on `app.state.clients`. Routes look clients up by name; they don't construct sessions on demand. On shutdown `lifespan` closes everything via the client's `AsyncExitStack`. **If you add a new MCP transport or persistent resource, plug it into the existing `AsyncExitStack` in `MCPClient` — do not introduce a parallel cleanup path.**
- **Logging is configured exactly once — `src/andela_mcp/logging.py`.** `configure_logging()` is called from `create_app` (and from `tests/conftest.py` as a session-autouse fixture). When `log_format=json` it adds a `severity` field so Cloud Run / Cloud Logging recognize levels; `console` is for local dev. A request-id middleware binds `request_id`, `method`, `path` into structlog contextvars for the duration of each request — emit logs via `get_logger()` and they inherit that context automatically.
- **The MCP client is transport-agnostic — `src/andela_mcp/client.py`.** Adding a transport = one more `case` in `MCPClient.connect()` plus an enum value. Always use `MCPClient` as an async context manager (or call `connect()`/`close()` symmetrically) — bypassing it leaks subprocesses on stdio transports.

## Deployment model

GitHub Actions → GCP via **Workload Identity Federation** (no service-account keys). The flow is intentionally split:

1. **Bootstrap** (one-time, manual, from a privileged workstation): `terraform apply` provisions the WIF pool/provider, the `*-deploy` service account, and the `principalSet` binding scoped to `attribute.repository == github_repository`. The `*-run` service account (Cloud Run runtime identity) is provisioned in the same apply.
2. **CI/CD** (`.github/workflows/deploy.yml`): authenticates via `google-github-actions/auth@v2` using the WIF provider, builds + pushes the image to Artifact Registry, then runs `terraform apply` again to roll the new image onto Cloud Run.

The deploy workflow needs these GitHub repo **Variables** set (not Secrets — they're non-sensitive identifiers): `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOYER_SERVICE_ACCOUNT`, `TF_STATE_BUCKET`. The first apply's outputs tell you what to paste.

Secrets (e.g. `ANDELA_MCP_ANTHROPIC_API_KEY`) are stored in **Secret Manager** and mounted into the Cloud Run container via the `secrets` Terraform variable (`map[ENV_VAR] = secret_name`). Do not pass secrets through `env`.

## Conventions worth respecting

- **`from __future__ import annotations` at the top of every module.** Already consistent across `src/`; keep it.
- **mypy is strict** (`strict = true` in pyproject). New code must type-check cleanly. Use `pydantic.BaseModel` / `pydantic-settings.BaseSettings` for any structured config or request/response shape.
- **No module-level side effects** beyond `app = create_app()` in `server.py`. Tests import freely; don't break that.
- **Terraform: never commit `*.tfvars`** — only `*.tfvars.example`. The `.gitignore` already enforces this; don't loosen it.
- **Workflow injection hygiene:** in GitHub Actions `run:` blocks, never interpolate `${{ github.event.* }}` user-content fields directly. Read them into `env:` first, then reference as `$VAR`. The current workflows only use `vars.*`, `github.sha`, `github.repository`, and a constrained `workflow_dispatch` choice — keep it that way.

## When changing things

- Adding a route → put it inside `create_app` so it shares the request-id middleware and lifespan-managed clients.
- Adding a setting → add it to `Settings`, document it in `env.example`, and (if non-secret) wire it into the `env` map in `infra/terraform/envs/*.tfvars`. If it's secret, add it under `secrets` and create the Secret Manager secret out-of-band.
- Adding a dependency → `uv add <pkg>` (or edit `pyproject.toml` and run `uv sync`); update the `mypy` `additional_dependencies` list in `.pre-commit-config.yaml` if it ships type stubs the hook needs.
- Bumping the Terraform provider or Python version → update `infra/terraform/versions.tf`, `infra/terraform/.terraform-version`, `.python-version`, `pyproject.toml`'s `requires-python`, the `python:3.12-slim` base in `Dockerfile`, and the `uv python install` line in CI together. They're coupled.
