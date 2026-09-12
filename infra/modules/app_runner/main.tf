locals {
  ecr_repository_url = var.create_ecr_repository ? aws_ecr_repository.backend[0].repository_url : null
  image_identifier = (
    var.image_identifier != null ? var.image_identifier :
    local.ecr_repository_url != null ? "${local.ecr_repository_url}:${var.image_tag}" : null
  )
}

# ---------------------------------------------------------------------------
# ECR repository for the FastAPI image (backend/Dockerfile, see PR #10)
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "backend" {
  count = var.create_ecr_repository ? 1 : 0

  name                 = "${var.name}-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "backend" {
  count = var.create_ecr_repository ? 1 : 0

  repository = aws_ecr_repository.backend[0].name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the most recent ${var.ecr_images_to_keep} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_images_to_keep
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# IAM - access role (App Runner pulls the image from ECR)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "access_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "access" {
  name               = "${var.name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.access_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "access_ecr" {
  role       = aws_iam_role.access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ---------------------------------------------------------------------------
# IAM - instance (task) role: what the running FastAPI process may do
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "secrets_read" {
  statement {
    sid    = "ReadRuntimeSecrets"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = var.secret_arns
  }

  dynamic "statement" {
    for_each = var.secrets_kms_key_arn == null ? [] : [var.secrets_kms_key_arn]
    content {
      sid       = "DecryptSecrets"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "instance_secrets" {
  name   = "secrets-manager-read"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

resource "aws_iam_role_policy_attachment" "instance_uploads" {
  role       = aws_iam_role.instance.name
  policy_arn = var.uploads_policy_arn
}

# ---------------------------------------------------------------------------
# Networking: VPC connector places egress ENIs in the private subnets so the
# service can reach RDS; the security group restricts that to 5432 + HTTPS.
# ---------------------------------------------------------------------------

resource "aws_apprunner_vpc_connector" "this" {
  vpc_connector_name = "${var.name}-connector"
  subnets            = var.private_subnet_ids
  security_groups    = [var.security_group_id]

  tags = var.tags
}

resource "aws_apprunner_auto_scaling_configuration_version" "this" {
  auto_scaling_configuration_name = "${var.name}-asc"
  max_concurrency                 = var.max_concurrency
  min_size                        = var.min_size
  max_size                        = var.max_size

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Service
#
# NOTE: App Runner enforces a fixed 120-second synchronous request timeout
# that is not configurable through any API; the service therefore already runs
# at the platform maximum (FOOD-51). Long receipt/AI work is handled as async
# jobs at the application level. See infra/README.md ("Request timeout").
# ---------------------------------------------------------------------------

resource "aws_apprunner_service" "this" {
  count = var.create_service ? 1 : 0

  service_name = var.name

  source_configuration {
    auto_deployments_enabled = var.auto_deployments_enabled

    authentication_configuration {
      access_role_arn = aws_iam_role.access.arn
    }

    image_repository {
      image_identifier      = local.image_identifier
      image_repository_type = "ECR"

      image_configuration {
        port                          = tostring(var.container_port)
        runtime_environment_variables = var.environment_variables
        runtime_environment_secrets   = var.runtime_secrets
      }
    }
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = var.health_check_path
    interval            = var.health_check_interval
    timeout             = var.health_check_timeout
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  network_configuration {
    ip_address_type = "IPV4"

    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.this.arn
    }

    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.this.arn

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.access_ecr,
    aws_iam_role_policy.instance_secrets,
    aws_iam_role_policy_attachment.instance_uploads,
  ]

  lifecycle {
    precondition {
      condition     = local.image_identifier != null
      error_message = "Set image_identifier or enable create_ecr_repository so an image URI can be derived."
    }
  }
}

# Optional custom domain (e.g. api.example.com). Serving the API on a sibling
# subdomain of the frontend keeps session cookies same-site when the browser
# calls App Runner directly (see README: FOOD-48 / FOOD-50).
resource "aws_apprunner_custom_domain_association" "this" {
  count = var.create_service && var.custom_domain != null ? 1 : 0

  service_arn          = aws_apprunner_service.this[0].arn
  domain_name          = var.custom_domain
  enable_www_subdomain = false
}
