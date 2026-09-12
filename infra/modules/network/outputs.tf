output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "app_runner_security_group_id" {
  description = "Attach to the App Runner VPC connector."
  value       = aws_security_group.app_runner.id
}

output "database_security_group_id" {
  description = "Attach to the RDS instance."
  value       = aws_security_group.database.id
}

output "nat_gateway_public_ips" {
  description = "Public IPs of the NAT gateway(s) - outbound IP(s) of the backend."
  value       = aws_eip.nat[*].public_ip
}
