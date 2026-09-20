output "secret_arns" {
  description = "Map of key -> secret ARN."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Map of key -> full secret name."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}
