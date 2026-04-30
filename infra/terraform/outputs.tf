output "service_url" {
  description = "Cloud Run service URL."
  value       = google_cloud_run_v2_service.app.uri
}

output "artifact_registry_repository" {
  description = "Artifact Registry path for pushing images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "runtime_service_account" {
  description = "Cloud Run runtime service account email."
  value       = google_service_account.runtime.email
}

output "deployer_service_account" {
  description = "Service account assumed by GitHub Actions via WIF."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Full WIF provider resource name; pass to google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_actions_variables" {
  description = "Paste these into GitHub → Settings → Secrets and variables → Actions → Variables tab."
  value = {
    GCP_PROJECT_ID                 = var.project_id
    GCP_PROJECT_NUMBER             = var.project_number
    GCP_REGION                     = var.region
    GCP_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOYER_SERVICE_ACCOUNT   = google_service_account.deployer.email
    TF_STATE_BUCKET                = "${var.project_id}-tf-state"
  }
}
