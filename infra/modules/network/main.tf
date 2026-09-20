data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Public subnets occupy the first /24s of the VPC CIDR, private subnets the
  # next block, so adding AZs never renumbers an existing subnet.
  public_subnet_cidrs  = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnet_cidrs = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i + 100)]

  nat_gateway_count = var.enable_nat_gateway ? (var.single_nat_gateway ? 1 : var.az_count) : 0
}

# ---------------------------------------------------------------------------
# VPC + internet gateway
# ---------------------------------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "${var.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-igw" })
}

# ---------------------------------------------------------------------------
# Subnets
# ---------------------------------------------------------------------------

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${var.name}-public-${local.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = merge(var.tags, {
    Name = "${var.name}-private-${local.azs[count.index]}"
    Tier = "private"
  })
}

# ---------------------------------------------------------------------------
# NAT (private subnets need outbound internet for Anthropic / OpenAI / Resend
# because App Runner routes *all* egress through the VPC connector)
# ---------------------------------------------------------------------------

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-public-rt" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-private-rt-${local.azs[count.index]}" })
}

resource "aws_route" "private_nat" {
  count = var.enable_nat_gateway ? var.az_count : 0

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# S3 gateway endpoint: keeps upload traffic off the NAT gateway (free).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(var.tags, { Name = "${var.name}-s3-endpoint" })
}

# ---------------------------------------------------------------------------
# Security groups
#
# Two groups, wired so the *only* path into the database is TCP/5432 from the
# App Runner VPC connector. Rules are standalone resources to avoid a cycle
# between the two groups.
# ---------------------------------------------------------------------------

resource "aws_security_group" "app_runner" {
  name        = "${var.name}-app-runner"
  description = "App Runner VPC connector ENIs (FastAPI backend)"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-app-runner" })
}

resource "aws_security_group" "database" {
  name        = "${var.name}-database"
  description = "RDS PostgreSQL - accepts 5432 from App Runner only"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-database" })
}

resource "aws_vpc_security_group_ingress_rule" "database_from_app_runner" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.app_runner.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from App Runner"

  tags = merge(var.tags, { Name = "${var.name}-db-from-app-runner-5432" })
}

resource "aws_vpc_security_group_egress_rule" "app_runner_to_database" {
  security_group_id            = aws_security_group.app_runner.id
  referenced_security_group_id = aws_security_group.database.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL to RDS"

  tags = merge(var.tags, { Name = "${var.name}-app-runner-to-db-5432" })
}

# HTTPS egress covers Anthropic, OpenAI, Resend, S3, Secrets Manager and ECR.
resource "aws_vpc_security_group_egress_rule" "app_runner_https" {
  security_group_id = aws_security_group.app_runner.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "HTTPS to external APIs and AWS services"

  tags = merge(var.tags, { Name = "${var.name}-app-runner-https" })
}
