output "region" {
  value = var.region
}

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

output "amplify_app_id" {
  value = try(module.amplify[0].app_id, null)
}

output "amplify_default_domain" {
  description = "<app-id>.amplifyapp.com"
  value       = try(module.amplify[0].default_domain, null)
}

output "amplify_branch_url" {
  description = "Production branch URL on the Amplify default domain."
  value       = try(module.amplify[0].branch_url, null)
}

output "frontend_url" {
  description = "Custom-domain URL when configured, else the Amplify branch URL. Feed this back into var.frontend_url (FOOD-50)."
  value       = try(module.amplify[0].frontend_url, null)
}

output "amplify_domain_verification_record" {
  value = try(module.amplify[0].domain_verification_records, null)
}

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

output "app_runner_service_url" {
  description = "Public HTTPS base URL of the FastAPI service (null until create_app_runner_service = true)."
  value       = module.app_runner.service_url
}

output "app_runner_service_arn" {
  value = module.app_runner.service_arn
}

output "backend_url" {
  description = "URL the frontend should use for the API: custom domain if set, else the App Runner URL (FOOD-48)."
  value       = local.backend_url
}

output "backend_custom_domain_dns_target" {
  description = "CNAME target for backend_custom_domain (null when unused)."
  value       = module.app_runner.custom_domain_dns_target
}

output "backend_custom_domain_certificate_validation_records" {
  value = module.app_runner.custom_domain_certificate_validation_records
}

output "ecr_repository_url" {
  description = "Push the backend image here (docker build ./backend, tag, push)."
  value       = module.app_runner.ecr_repository_url
}

output "backend_image_identifier" {
  value = module.app_runner.image_identifier
}

output "app_runner_instance_role_arn" {
  value = module.app_runner.instance_role_arn
}

output "backend_frontend_url" {
  description = "FRONTEND_URL given to App Runner (FOOD-50)."
  value       = local.frontend_url
}

output "backend_cors_origins" {
  description = "CORS_ORIGINS given to App Runner (FOOD-50)."
  value       = local.cors_origins
}

output "backend_runtime_secret_env_vars" {
  description = "Env vars App Runner populates from Secrets Manager (FOOD-50)."
  value       = module.app_runner.runtime_secret_env_vars
}

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

output "rds_endpoint" {
  description = "host:port of the PostgreSQL instance (private - reachable only from the VPC)."
  value       = module.database.endpoint
}

output "rds_address" {
  value = module.database.address
}

output "rds_database_name" {
  value = module.database.database_name
}

output "uploads_bucket_name" {
  value = module.storage.bucket_name
}

output "uploads_bucket_arn" {
  value = module.storage.bucket_arn
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

output "secret_arns" {
  description = "Secrets Manager ARNs keyed by short name (database_url, auth_secret, anthropic, openai, resend)."
  value       = module.secrets.secret_arns
}

output "secret_names" {
  value = module.secrets.secret_names
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "nat_gateway_public_ips" {
  description = "Outbound IPs of the backend (allow-list these with third parties if needed)."
  value       = module.network.nat_gateway_public_ips
}
