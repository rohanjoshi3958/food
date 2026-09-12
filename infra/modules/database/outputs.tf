output "endpoint" {
  description = "host:port"
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Hostname only."
  value       = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "master_password" {
  value     = random_password.master.result
  sensitive = true
}

output "database_url" {
  description = "SQLAlchemy/psycopg2 URL. TLS is enforced server-side via rds.force_ssl."
  value       = "postgresql://${aws_db_instance.this.username}:${urlencode(random_password.master.result)}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?sslmode=require"
  sensitive   = true
}

output "instance_arn" {
  value = aws_db_instance.this.arn
}

output "instance_id" {
  value = aws_db_instance.this.id
}
