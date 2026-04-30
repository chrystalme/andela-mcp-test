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

bold "==> 4/5  Loading secrets from $ENV_FILE"
declare -A SECRETS
SECRET_KEYS=(ANDELA_MCP_GROQ_API_KEY ANDELA_MCP_OPENAI_API_KEY ANDELA_MCP_REMOTE_TOKEN)
if [ -f "$ENV_FILE" ]; then
  for key in "${SECRET_KEYS[@]}"; do
    val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    val="${val%\"}"; val="${val#\"}"   # strip optional surrounding quotes
    if [ -n "$val" ]; then
      SECRETS[$key]="$val"
      green "  $key (loaded)"
    else
      yellow "  $key (empty / missing)"
    fi
  done
else
  yellow "  $ENV_FILE not found — no secrets will be created. Set them in .env and re-run."
fi

# Build the secret_values JSON for TF_VAR_secret_values.
SECRET_JSON="{"
first=1
for k in "${!SECRETS[@]}"; do
  v="${SECRETS[$k]}"
  esc="${v//\\/\\\\}"; esc="${esc//\"/\\\"}"
  [ $first -eq 1 ] || SECRET_JSON+=","
  SECRET_JSON+="\"$k\":\"$esc\""
  first=0
done
SECRET_JSON+="}"

bold "==> 5/5  Terraform init + apply ($ENV)"
cd "$TF_DIR"

# Auto-create envs/<env>.tfvars from the example if missing, with project_id pre-filled.
TFVARS="envs/${ENV}.tfvars"
if [ ! -f "$TFVARS" ]; then
  cp "envs/dev.tfvars.example" "$TFVARS"
  yellow "  generated $TFVARS from dev.tfvars.example — review before re-running"
fi

terraform init -reconfigure \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=andela-mcp/${ENV}"

TF_VAR_project_id="$PROJECT_ID" \
TF_VAR_project_number="$PROJECT_NUMBER" \
TF_VAR_github_repository="$GITHUB_REPO" \
TF_VAR_secret_values="$SECRET_JSON" \
  terraform apply -auto-approve -var-file="$TFVARS"

bold "==> Done. GitHub Actions variables (needed by .github/workflows/deploy.yml for OIDC):"
GH_VARS_JSON="$(terraform output -json github_actions_variables)"
echo "$GH_VARS_JSON" | python3 -c 'import json,sys; [print(f"  {k} = {v}") for k,v in json.load(sys.stdin).items()]'

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  bold "==> Pushing those variables to GitHub via gh CLI"
  echo "$GH_VARS_JSON" | python3 -c '
import json, sys, subprocess
vars = json.load(sys.stdin)
for k, v in vars.items():
    subprocess.run(["gh", "variable", "set", k, "--body", v], check=True)
    print(f"  set {k}")
'
  green "  Variables pushed. Trigger a deploy with: gh workflow run deploy.yml -f environment=$ENV"
else
  yellow "  gh CLI not installed or not authenticated — paste the variables manually."
  yellow "  (install: https://cli.github.com/  ;  auth: gh auth login)"
fi

bold "==> Cloud Run URL:"
terraform output -raw service_url
echo
