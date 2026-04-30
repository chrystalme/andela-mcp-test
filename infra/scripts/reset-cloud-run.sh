#!/usr/bin/env bash
# Recover from a Cloud Run service stuck in deletion-protected state during
# bootstrap. Idempotent: every step continues on failure ("already gone" is OK).
#
# Then re-runs `make tf-up` so Terraform recreates the service from scratch
# with the corrected configuration (deletion_protection=false, real project_id).

set -uo pipefail

PROJECT="${PROJECT:-chatbot-andela}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-andela-mcp-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$REPO_ROOT/infra/terraform"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

bold "==> 1/4  Disable deletion-protection on the Cloud Run service (gcloud)"
if gcloud run services update "$SERVICE" \
     --region="$REGION" --project="$PROJECT" \
     --no-deletion-protection 2>/dev/null; then
  green "  protection cleared via gcloud"
elif gcloud beta run services update "$SERVICE" \
       --region="$REGION" --project="$PROJECT" \
       --no-deletion-protection 2>/dev/null; then
  green "  protection cleared via gcloud beta"
else
  # Fall back to the REST API directly (always available, no flag dependency).
  yellow "  gcloud flag unavailable — falling back to REST API"
  TOKEN="$(gcloud auth application-default print-access-token 2>/dev/null || gcloud auth print-access-token)"
  HTTP_CODE=$(curl -s -o /tmp/cr-patch.out -w '%{http_code}' \
    -X PATCH \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/services/${SERVICE}?updateMask=deletionProtection" \
    -d '{"deletionProtection": false}')
  if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
    green "  REST patch succeeded (HTTP $HTTP_CODE)"
  else
    red "  REST patch returned HTTP $HTTP_CODE — output:"
    cat /tmp/cr-patch.out; echo
    yellow "  continuing anyway; later steps may still recover"
  fi
fi

bold "==> 2/4  Delete the Cloud Run service from GCP"
if gcloud run services delete "$SERVICE" \
     --region="$REGION" --project="$PROJECT" --quiet 2>&1 | tail -1; then
  green "  GCP service deleted (or already absent)"
else
  yellow "  delete returned non-zero — continuing"
fi

bold "==> 3/4  Drop the resource from Terraform state"
if (cd "$TF_DIR" && terraform state rm google_cloud_run_v2_service.app) 2>&1 | tail -1; then
  green "  removed from state (or wasn't tracked)"
else
  yellow "  state rm reported nothing to remove"
fi

bold "==> 4/4  Re-run make tf-up"
cd "$REPO_ROOT"
exec make tf-up
