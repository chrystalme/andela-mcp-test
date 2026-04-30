#!/usr/bin/env bash
# Provision the GCP environment for andela-mcp end-to-end.
#
# Auto-detects:
#   - GCP project from `gcloud config get-value project`
#   - Project number from `gcloud projects describe`
#   - GitHub repo from `git remote get-url origin`
#   - Secrets from <repo-root>/.env (ANDELA_MCP_GROQ_API_KEY, ANDELA_MCP_OPENAI_API_KEY,
#     ANDELA_MCP_REMOTE_TOKEN). Empty values are skipped.
#
# Creates:
#   - GCS bucket for Terraform state (idempotent, with versioning)
#   - WIF pool/provider, deployer + runtime service accounts
#   - Artifact Registry repo
#   - Secret Manager secrets (with first version) for non-empty .env values
#   - Cloud Run service (placeholder image until CI builds the real one)
#
# Prints copy-paste GitHub Actions variables on success.

set -euo pipefail

ENV="${ENV:-dev}"
TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../terraform" && pwd)"
REPO_ROOT="$(cd "$TF_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

require() {
  command -v "$1" >/dev/null 2>&1 || { red "missing required command: $1"; exit 1; }
}

require gcloud
require terraform
require gsutil
require git

bold "==> 1/5  Detecting GCP project"
PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
[ -n "$PROJECT_ID" ] || { red "no gcloud project set. run: gcloud config set project <id>"; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
green "  project_id     = $PROJECT_ID"
green "  project_number = $PROJECT_NUMBER"

bold "==> 2/5  Detecting GitHub repo"
ORIGIN_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
GITHUB_REPO="$(echo "$ORIGIN_URL" | sed -E 's#(git@github.com:|https://github.com/)([^.]+)(\.git)?$#\2#')"
[ -n "$GITHUB_REPO" ] && [[ "$GITHUB_REPO" == *"/"* ]] || {
  red "could not parse OWNER/REPO from git remote 'origin' (got: $ORIGIN_URL)"
  red "fix: GITHUB_REPO=OWNER/REPO $0"
  GITHUB_REPO="${GITHUB_REPO_OVERRIDE:-${GITHUB_REPO:-}}"
}
green "  github_repository = $GITHUB_REPO"

bold "==> 3/5  Ensuring Terraform state bucket"
STATE_BUCKET="${PROJECT_ID}-tf-state"
if gsutil ls -p "$PROJECT_ID" -b "gs://$STATE_BUCKET" >/dev/null 2>&1; then
  green "  gs://$STATE_BUCKET already exists"
else
  REGION="${REGION:-us-central1}"
  yellow "  creating gs://$STATE_BUCKET in $REGION ..."
  gsutil mb -p "$PROJECT_ID" -l "$REGION" -b on "gs://$STATE_BUCKET"
  gsutil versioning set on "gs://$STATE_BUCKET"
fi

bold "==> 4/5  Syncing secrets from $ENV_FILE to Secret Manager"
# Secret naming convention (must match dev.tfvars.example's `secrets` map):
#   ${SERVICE}-${ENV}-<envvar lowercased, underscores -> dashes>
SERVICE_NAME="andela-mcp"
SECRET_KEYS=(ANDELA_MCP_GROQ_API_KEY ANDELA_MCP_OPENAI_API_KEY ANDELA_MCP_REMOTE_TOKEN)

if [ ! -f "$ENV_FILE" ]; then
  yellow "  $ENV_FILE not found — skipping secret sync."
else
  # Make sure the API is enabled (idempotent — fast no-op if already enabled).
  gcloud services enable secretmanager.googleapis.com --project="$PROJECT_ID" >/dev/null 2>&1 || true

  for key in "${SECRET_KEYS[@]}"; do
    val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    val="${val%\"}"; val="${val#\"}"   # strip optional surrounding quotes
    if [ -z "$val" ]; then
      yellow "  $key (empty / missing) — skipped"
      continue
    fi

    secret_name="${SERVICE_NAME}-${ENV}-$(printf %s "$key" | tr '[:upper:]_' '[:lower:]-')"

    # Create the secret if it doesn't exist (ignore the "already exists" error).
    gcloud secrets create "$secret_name" \
      --project="$PROJECT_ID" --replication-policy=automatic \
      >/dev/null 2>&1 || true

    # Add a new version. SM keeps the old ones; "latest" alias auto-rolls.
    if printf %s "$val" | gcloud secrets versions add "$secret_name" \
         --project="$PROJECT_ID" --data-file=- >/dev/null 2>&1; then
      green "  $key -> $secret_name"
    else
      red "  $key (failed to add version to $secret_name)"
    fi
  done
fi

bold "==> 5/5  Terraform init + apply ($ENV)"
cd "$TF_DIR"

# Auto-create envs/<env>.tfvars from the example if missing, with project_id pre-filled.
TFVARS="envs/${ENV}.tfvars"
EXAMPLE="envs/${ENV}.tfvars.example"
[ -f "$EXAMPLE" ] || { red "  $EXAMPLE not found — create it before bootstrapping ENV=$ENV"; exit 1; }
if [ ! -f "$TFVARS" ]; then
  cp "$EXAMPLE" "$TFVARS"
  yellow "  generated $TFVARS from $EXAMPLE — review before re-running"
fi

terraform init -reconfigure \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=andela-mcp/${ENV}"

# Pass auto-detected values via -var (highest precedence — beats anything in $TFVARS).
terraform apply -auto-approve \
  -var-file="$TFVARS" \
  -var="project_id=$PROJECT_ID" \
  -var="project_number=$PROJECT_NUMBER" \
  -var="github_repository=$GITHUB_REPO"

# Grant the deployer SA storage.objectAdmin on the state bucket so the deploy
# workflow's `terraform init` against the GCS backend can read/write state.
# Idempotent: gcloud add-iam-policy-binding silently no-ops if the role is
# already bound.
DEPLOYER_SA="$(terraform output -raw deployer_service_account 2>/dev/null || true)"
if [ -n "$DEPLOYER_SA" ]; then
  bold "==> Granting state bucket access to $DEPLOYER_SA"
  gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role=roles/storage.objectAdmin \
    --project="$PROJECT_ID" >/dev/null
  green "  granted roles/storage.objectAdmin"
fi

bold "==> Done. GitHub Actions variables (needed by .github/workflows/deploy.yml for OIDC):"
GH_VARS_JSON="$(terraform output -json github_actions_variables)"
echo "$GH_VARS_JSON" | python3 -c 'import json,sys; [print(f"  {k} = {v}") for k,v in json.load(sys.stdin).items()]'

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  bold "==> Pushing those variables to GitHub via gh CLI (scoped to env: $ENV)"
  # Scope to the env so per-environment values don't clobber each other at the
  # repo level. WIF provider goes in `secrets`, the rest in `variables`.
  echo "$GH_VARS_JSON" | ENV="$ENV" python3 -c '
import json, os, sys, subprocess
env = os.environ["ENV"]
data = json.load(sys.stdin)
secret_keys = {"GCP_WORKLOAD_IDENTITY_PROVIDER"}
for k, v in data.items():
    cmd_kind = "secret" if k in secret_keys else "variable"
    subprocess.run(["gh", cmd_kind, "set", k, "--env", env, "--body", v], check=True)
    print(f"  set {cmd_kind} {k} on env {env}")
'
  green "  Variables pushed. Trigger a deploy with: gh workflow run deploy.yml -f environment=$ENV"
else
  yellow "  gh CLI not installed or not authenticated — paste the variables manually."
  yellow "  (install: https://cli.github.com/  ;  auth: gh auth login)"
fi

bold "==> Cloud Run URL:"
terraform output -raw service_url
echo
