# Initialize with:
#   terraform init \
#     -backend-config="bucket=<tf-state-bucket>" \
#     -backend-config="prefix=andela-mcp/${ENV}"
#
# Bucket must be pre-created with versioning + uniform bucket-level access enabled.
terraform {
  backend "gcs" {}
}
