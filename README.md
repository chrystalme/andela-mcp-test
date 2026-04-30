# andela-mcp

A Python service that consumes upstream Model Context Protocol (MCP) servers, re-exposes their tools over a thin HTTP API, and ships a conversational chatbot (with a floating-button frontend) backed by the OpenAI Agents SDK + Groq. Deployed to Cloud Run; CI/CD authenticates to GCP via Workload Identity Federation (no static keys).

## Stack

- **Runtime:** Python 3.12, FastAPI, [`mcp`](https://pypi.org/project/mcp/) Python SDK, structlog
- **Chat agent:** [`openai-agents`](https://pypi.org/project/openai-agents/) SDK; inference via Groq (default model `openai/gpt-oss-120b`); optional trace export to OpenAI
- **Frontend:** single static HTML page with vanilla-JS floating chat widget (markdown rendered via `marked` + `DOMPurify`)
- **Tooling:** [uv](https://docs.astral.sh/uv/) (deps), ruff (lint+format), mypy (strict), pytest+coverage, pre-commit, gitleaks
- **Container:** multi-stage Dockerfile, non-root user, healthcheck
- **Infra:** Terraform ≥ 1.10 → Cloud Run, Artifact Registry, Secret Manager, IAM, Workload Identity Federation
- **CI/CD:** GitHub Actions (lint/type/test/tf-validate/docker-build on every PR; build+push+`terraform apply` on `main`/tags via OIDC)

## Endpoints

| Path | Description |
|---|---|
| `GET /` | Landing page with floating chat widget (talks to `/v1/chat`) |
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness; lists connected MCP servers |
| `GET /v1/tools` | Per-server tool catalog |
| `POST /v1/tools/call` | Invoke a tool by `(server, tool, arguments)` |
| `POST /v1/chat` | Conversational agent over the MCP tool surface |

## Local development

```bash
make install        # uv sync + pre-commit install
cp env.example .env # then fill in ANDELA_MCP_GROQ_API_KEY at minimum
make run            # http://127.0.0.1:8080/  (the chat widget)
```

Common targets:

```bash
make check            # lint + typecheck + test
make fmt              # ruff fix + format
make docker-build
make tf-up ENV=dev    # one-command GCP bootstrap (see below)
```

## Configuration

All settings load from environment variables prefixed `ANDELA_MCP_` (see `src/andela_mcp/config.py`). Most useful:

| Var | Purpose | Required for |
|---|---|---|
| `ANDELA_MCP_GROQ_API_KEY` | Inference (chat agent) | `/v1/chat` |
| `ANDELA_MCP_OPENAI_API_KEY` | Agents-SDK trace export to OpenAI dashboard | optional |
| `ANDELA_MCP_LLM_MODEL` | Default `openai/gpt-oss-120b` | optional |
| `ANDELA_MCP_SERVERS_CONFIG_PATH` | Path to MCP server registry (`config/servers.json`) | always |
| `ANDELA_MCP_REMOTE_TOKEN` | Bearer for the order-mcp upstream | when wired into `headers` |
| `ANDELA_MCP_LOG_FORMAT` | `console` (dev) or `json` (Cloud Run) | optional |

## Production deployment to GCP — one command

The whole bootstrap is automated. Prereqs:

- `gcloud auth login && gcloud auth application-default login`
- `gcloud config set project <PROJECT_ID>` (e.g. `chatbot-andela`)
- `terraform >= 1.10` installed
- `gh` CLI authenticated (optional — used to push GitHub Variables for OIDC)
- `.env` populated with the secret values you want stored in Secret Manager

Then:

```bash
make tf-up ENV=dev
```

`infra/scripts/bootstrap.sh` will:

1. Detect `project_id`, `project_number`, `github_repository` automatically.
2. Create the GCS Terraform state bucket (`<project_id>-tf-state`) if missing.
3. Read secret values from `.env` and pass them to Terraform via `TF_VAR_secret_values`.
4. `terraform init` + `apply` — provisions WIF pool/provider, deployer + runtime service accounts, Artifact Registry, Secret Manager secrets, and a Cloud Run service running a placeholder image (real image rolls in on the next CI deploy).
5. Push the six GitHub Actions Variables (`GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOYER_SERVICE_ACCOUNT`, `TF_STATE_BUCKET`) into your repo via `gh` — these are what `.github/workflows/deploy.yml` uses for OIDC auth.

After that, push to `main` and CI takes over: build → push to Artifact Registry → `terraform apply` → Cloud Run rollout. No service-account JSON keys exist anywhere.

If you don't have `gh` installed, the script prints the variables instead — paste them into Settings → Secrets and variables → Actions → Variables.

## Layout

```
src/andela_mcp/
  server.py        FastAPI app, request-id middleware, lifespan
  client.py        MCPClient — stdio / streamable-HTTP / SSE
  chat.py          ChatService — Agents SDK + Groq + MCP-tools-as-FunctionTools
  config.py        Settings (pydantic-settings, prefix ANDELA_MCP_)
  logging.py       structlog config
  static/
    index.html     Landing page + floating chat widget

tests/{unit,integration}/

infra/
  terraform/       Cloud Run + WIF + Artifact Registry + Secret Manager
  scripts/
    bootstrap.sh   One-shot GCP bootstrap (called by `make tf-up`)

.github/workflows/
  ci.yml           Lint, typecheck, test, tf-validate, docker build (PRs)
  deploy.yml       Build + push + apply on main / tags (OIDC via WIF)

mcp-tools.ipynb    Notebook for exploring upstream MCP servers
```
