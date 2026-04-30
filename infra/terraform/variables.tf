variable "project_id" {
  type        = string
  description = "GCP project ID."
}

variable "project_number" {
  type        = string
  description = "GCP project number (used by Workload Identity Federation principalSet)."
}

variable "region" {
  type        = string
  description = "Primary GCP region."
  default     = "us-central1"
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev | staging | prod)."
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name."
  default     = "andela-mcp"
}

variable "image" {
  type        = string
  description = "Fully qualified container image. Defaults to a Cloud Run hello placeholder so the first `terraform apply` succeeds before CI has built/pushed the real image."
  default     = "gcr.io/cloudrun/hello"
}

variable "min_instances" {
  type    = number
  default = 0
}

variable "max_instances" {
  type    = number
  default = 5
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed to assume the WIF service account, in OWNER/REPO form."
}

variable "allow_unauthenticated" {
  type        = bool
  description = "If true, grants roles/run.invoker to allUsers. Keep false for internal services."
  default     = false
}

variable "secrets" {
  type        = map(string)
  description = "Map of ENV_VAR_NAME -> Secret Manager secret name (no version). Mounted as 'latest'."
  default     = {}
}

variable "secret_values" {
  type        = map(string)
  description = "Map of ENV_VAR_NAME -> plaintext secret value. When set, Terraform creates the matching Secret Manager secret AND its first version. Keys must overlap with `secrets`. Provide via gitignored `secrets.auto.tfvars` or `TF_VAR_secret_values`."
  default     = {}
  sensitive   = true
}

variable "env" {
  type        = map(string)
  description = "Plain (non-secret) environment variables passed to the container."
  default     = {}
}
