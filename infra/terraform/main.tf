locals {
  labels = {
    app         = var.service_name
    environment = var.environment
    managed_by  = "terraform"
  }

  # Secrets fall into two buckets:
  #   - var.secrets       : pre-existing Secret Manager secrets, supplied as ENV_VAR -> secret_id.
  #   - var.secret_values : plaintexts; Terraform creates the secret + first version for each.
  # The Cloud Run service mounts both via the merged map below.
  managed_secret_ids = { for k, v in google_secret_manager_secret.managed : k => v.secret_id }
  effective_secrets  = merge(var.secrets, local.managed_secret_ids)
}

resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "sts.googleapis.com",
  ])
  project                    = var.project_id
  service                    = each.key
  disable_dependent_services = false
  disable_on_destroy         = false
}

resource "google_artifact_registry_repository" "images" {
  provider      = google-beta
  project       = var.project_id
  location      = var.region
  repository_id = "${var.service_name}-${var.environment}"
  format        = "DOCKER"
  description   = "Container images for ${var.service_name} (${var.environment})"
  labels        = local.labels

  depends_on = [google_project_service.services]
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.service_name}-${var.environment}-run"
  display_name = "${var.service_name} (${var.environment}) Cloud Run runtime"
}

resource "google_project_iam_member" "runtime_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret" "managed" {
  for_each  = var.secret_values
  project   = var.project_id
  secret_id = "${var.service_name}-${var.environment}-${replace(lower(each.key), "_", "-")}"

  replication {
    auto {}
  }

  labels     = local.labels
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "managed" {
  for_each    = var.secret_values
  secret      = google_secret_manager_secret.managed[each.key].id
  secret_data = each.value
}

resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  for_each   = local.effective_secrets
  project    = var.project_id
  secret_id  = each.value
  role       = "roles/secretmanager.secretAccessor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
  depends_on = [google_secret_manager_secret.managed]
}

resource "google_iam_workload_identity_pool" "github" {
  provider                  = google-beta
  project                   = var.project_id
  workload_identity_pool_id = "github-${var.environment}"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "WIF pool for GitHub Actions deploying ${var.service_name}"

  depends_on = [google_project_service.services]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  provider                           = google-beta
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.actor"      = "assertion.actor"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "${var.service_name}-${var.environment}-deploy"
  display_name = "${var.service_name} (${var.environment}) GitHub Actions deployer"
}

resource "google_service_account_iam_member" "deployer_wif_binding" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github.workload_identity_pool_id}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  provider   = google-beta
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service" "app" {
  project  = var.project_id
  location = var.region
  name     = "${var.service_name}-${var.environment}"
  ingress  = "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  template {
    service_account                  = google_service_account.runtime.email
    max_instance_request_concurrency = 80
    timeout                          = "60s"

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.effective_secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get { path = "/healthz" }
        initial_delay_seconds = 2
        period_seconds        = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get { path = "/healthz" }
        period_seconds    = 30
        failure_threshold = 3
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_iam_member.runtime_logging,
    google_artifact_registry_repository.images,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
