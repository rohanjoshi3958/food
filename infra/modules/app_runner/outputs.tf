output "service_url" {
  description = "Default App Runner HTTPS URL (null until create_service = true)."
  value       = var.create_service ? "https://${aws_apprunner_service.this[0].service_url}" : null
}

output "custom_domain_url" {
  description = "https://<custom_domain> when configured, else null."
  value       = var.custom_domain == null ? null : "https://${var.custom_domain}"
}

output "custom_domain_dns_target" {
  description = "CNAME target for the custom domain."
  value       = try(aws_apprunner_custom_domain_association.this[0].dns_target, null)
}

output "custom_domain_certificate_validation_records" {
  description = "DNS records to create so App Runner can issue the TLS certificate."
  value       = try(aws_apprunner_custom_domain_association.this[0].certificate_validation_records, null)
}

output "service_arn" {
  value = var.create_service ? aws_apprunner_service.this[0].arn : null
}

output "service_id" {
  value = var.create_service ? aws_apprunner_service.this[0].service_id : null
}

output "ecr_repository_url" {
  value = local.ecr_repository_url
}

output "image_identifier" {
  value = local.image_identifier
}

output "runtime_secret_env_vars" {
  description = "Env var names populated from Secrets Manager at start."
  value       = sort(keys(var.runtime_secrets))
}

output "instance_role_arn" {
  value = aws_iam_role.instance.arn
}

output "access_role_arn" {
  value = aws_iam_role.access.arn
}

output "vpc_connector_arn" {
  value = aws_apprunner_vpc_connector.this.arn
}

output "effective_environment" {
  description = "Plain-text env vars passed to the service (no secrets)."
  value       = var.environment_variables
}
