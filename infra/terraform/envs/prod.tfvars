# `make tf-up ENV=prod` auto-detects project_id (gcloud), project_number, and
# github_repository (git remote) and passes them via -var on the command line,
# so they're omitted here.
region        = "us-central1"
environment   = "prod"
min_instances = 1            # warm to avoid cold-start latency on real traffic
max_instances = 10
cpu           = "2"
memory        = "1Gi"

env = {
  ANDELA_MCP_ENVIRONMENT = "prod"
  ANDELA_MCP_LOG_FORMAT  = "json"
  ANDELA_MCP_LLM_MODEL   = "openai/gpt-oss-120b"
}

# Naming follows infra/scripts/bootstrap.sh:
# ${service_name}-${environment}-<envvar lowercased, _->->.
secrets = {
  ANDELA_MCP_GROQ_API_KEY   = "andela-mcp-prod-andela-mcp-groq-api-key"
  ANDELA_MCP_OPENAI_API_KEY = "andela-mcp-prod-andela-mcp-openai-api-key"
}

allow_unauthenticated = true
