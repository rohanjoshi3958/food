# Master password is generated at apply time and lives only in Terraform state
# and Secrets Manager - never in git. RDS rejects '/', '@', '"' and spaces, and
# the root module URL-encodes the value when building DATABASE_URL.
resource "random_password" "master" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_subnet_group" "this" {
  name        = "${var.name}-db"
  description = "Private subnets for ${var.name} PostgreSQL"
  subnet_ids  = var.private_subnet_ids

  tags = merge(var.tags, { Name = "${var.name}-db" })
}

resource "aws_db_parameter_group" "this" {
  name        = "${var.name}-postgres16"
  family      = "postgres16"
  description = "PostgreSQL 16 parameters for ${var.name}"

  # Force TLS from the application; psycopg2 negotiates SSL by default.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier = "${var.name}-postgres"

  engine               = "postgres"
  engine_version       = var.engine_version
  parameter_group_name = aws_db_parameter_group.this.name
  instance_class       = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.database_name
  username = var.master_username
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  multi_az               = var.multi_az

  backup_retention_period    = var.backup_retention_days
  backup_window              = "06:00-07:00"
  maintenance_window         = "sun:07:30-sun:08:30"
  auto_minor_version_upgrade = true
  apply_immediately          = var.apply_immediately

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name}-postgres-final"
  copy_tags_to_snapshot     = true

  performance_insights_enabled    = var.performance_insights_enabled
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = merge(var.tags, { Name = "${var.name}-postgres" })
}
