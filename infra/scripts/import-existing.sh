#!/usr/bin/env bash
# Re-import GCP resources that exist but are missing from Terraform state.
# Idempotent — `terraform import` is a no-op if the address is already tracked
# (it errors but we tolerate that).

set -uo pipefail

ENV="${ENV:-dev}"
PROJECT="${PROJECT:-chatbot-andela}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-andela-mcp}"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

cd "$(dirname "${BASH_SOURCE[0]}")/../terraform"

import() {
  local addr="$1" id="$2"
  if terraform state show "$addr" >/dev/null 2>&1; then
    yellow "  $addr already in state — skipping"
    return 0
  fi
  if terraform import \
       -var="project_id=$PROJECT" \
       -var="project_number=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')" \
       -var="github_repository=$(git -C "$(git rev-parse --show-toplevel)" remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)([^.]+)(\.git)?$#\2#')" \
       -var-file="envs/${ENV}.tfvars" \
       "$addr" "$id" 2>&1 | tail -3; then
    green "  imported $addr"
  else
    yellow "  failed to import $addr (may already exist or resource missing)"
  fi
}

import 'google_service_account.runtime' \
  "projects/${PROJECT}/serviceAccounts/${SERVICE}-${ENV}-run@${PROJECT}.iam.gserviceaccount.com"

import 'google_service_account.deployer' \
  "projects/${PROJECT}/serviceAccounts/${SERVICE}-${ENV}-deploy@${PROJECT}.iam.gserviceaccount.com"

import 'google_artifact_registry_repository.images' \
  "projects/${PROJECT}/locations/${REGION}/repositories/${SERVICE}-${ENV}"

import 'google_iam_workload_identity_pool.github' \
  "projects/${PROJECT}/locations/global/workloadIdentityPools/github-${ENV}"

import 'google_iam_workload_identity_pool_provider.github' \
  "projects/${PROJECT}/locations/global/workloadIdentityPools/github-${ENV}/providers/github"

import 'google_cloud_run_v2_service.app' \
  "projects/${PROJECT}/locations/${REGION}/services/${SERVICE}-${ENV}"

green "==> Imports complete. Re-run \`make tf-up ENV=${ENV}\` to reconcile remaining IAM bindings."
