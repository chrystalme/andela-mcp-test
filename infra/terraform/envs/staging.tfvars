# `make tf-up ENV=staging` auto-detects project_id, project_number, github_repository.
region        = "us-central1"
environment   = "staging"
min_instances = 0
max_instances = 5

env = {
  ANDELA_MCP_ENVIRONMENT = "staging"
  ANDELA_MCP_LOG_FORMAT  = "json"
  ANDELA_MCP_LLM_MODEL   = "openai/gpt-oss-120b"
}

secrets = {
  ANDELA_MCP_GROQ_API_KEY   = "andela-mcp-staging-andela-mcp-groq-api-key"
  ANDELA_MCP_OPENAI_API_KEY = "andela-mcp-staging-andela-mcp-openai-api-key"
}

allow_unauthenticated = false
