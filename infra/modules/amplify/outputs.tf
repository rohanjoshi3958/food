output "app_id" {
  value = aws_amplify_app.this.id
}

output "app_arn" {
  value = aws_amplify_app.this.arn
}

output "default_domain" {
  description = "<app-id>.amplifyapp.com"
  value       = aws_amplify_app.this.default_domain
}

output "branch_url" {
  description = "https://<branch>.<app-id>.amplifyapp.com"
  value       = local.branch_url
}

output "custom_url" {
  description = "https URL on the custom domain, or null."
  value       = local.custom_url
}

output "frontend_url" {
  description = "Custom URL when configured, otherwise the Amplify branch URL."
  value       = coalesce(local.custom_url, local.branch_url)
}

output "domain_verification_records" {
  description = "DNS records Amplify needs for the custom domain (empty when no custom domain)."
  value       = try(aws_amplify_domain_association.this[0].certificate_verification_dns_record, null)
}

output "service_role_arn" {
  value = aws_iam_role.amplify.arn
}
