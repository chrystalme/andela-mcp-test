# andela-mcp

A Python service that consumes upstream Model Context Protocol (MCP) servers and exposes their tools over a thin HTTP API. Deployed to Cloud Run; CI/CD authenticates to GCP via Workload Identity Federation (no static keys).

## Stack

- **Runtime:** Python 3.12, FastAPI, [`mcp`](https://pypi.org/project/mcp/) Python SDK, structlog
- **Tooling:** [uv](https://docs.astral.sh/uv/) (deps), ruff (lint+format), mypy (strict), pytest+coverage, pre-commit, gitleaks
- **Container:** distroless-style multi-stage Dockerfile, non-root user, healthcheck
- **Infra:** Terraform 1.9 → Cloud Run, Artifact Registry, Secret Manager, IAM, Workload Identity Federation
- **CI/CD:** GitHub Actions (lint/type/test/tf-validate/docker-build on every PR; build+push+`terraform apply` on `main`/tags)

## Local development

```bash
make install        # uv sync + pre-commit install
cp env.example .env # then edit
make run            # http://127.0.0.1:8080/healthz
```

Common targets:

```bash
make check            # lint + typecheck + test
make fmt              # ruff fix + format
make docker-build
make tf-plan ENV=dev
```

## Configuration

All settings are loaded from environment variables prefixed `ANDELA_MCP_` (see `src/andela_mcp/config.py`). Upstream MCP servers are described in a JSON file pointed to by `ANDELA_MCP_SERVERS_CONFIG_PATH` (template at `config/servers.example.json`).

## Bootstrapping GCP / WIF

1. Create a state bucket: `gsutil mb -p PROJECT -l REGION gs://your-tf-state-bucket && gsutil versioning set on gs://your-tf-state-bucket`.
2. Copy `infra/terraform/envs/dev.tfvars.example` → `infra/terraform/envs/dev.tfvars` and fill in your values.
3. From a workstation with `roles/owner` (or equivalent), run:
   ```bash
   cd infra/terraform
   terraform init \
     -backend-config="bucket=your-tf-state-bucket" \
     -backend-config="prefix=andela-mcp/dev"
   terraform apply -var-file=envs/dev.tfvars
   ```
4. Read the outputs and configure GitHub repository **Variables** (Settings → Secrets and variables → Actions → Variables):
   - `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `GCP_REGION`
   - `GCP_WORKLOAD_IDENTITY_PROVIDER` ← `workload_identity_provider` output
   - `GCP_DEPLOYER_SERVICE_ACCOUNT` ← `deployer_service_account` output
   - `TF_STATE_BUCKET`
5. Subsequent deploys run from `.github/workflows/deploy.yml` — no service-account JSON keys required.

## Layout

```
src/andela_mcp/        FastAPI app, MCP client, config, logging
tests/{unit,integration}/
infra/terraform/       Cloud Run + WIF + Artifact Registry
.github/workflows/     ci.yml, deploy.yml
```
