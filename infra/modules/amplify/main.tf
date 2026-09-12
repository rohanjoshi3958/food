locals {
  default_build_spec = <<-YAML
    version: 1
    frontend:
      phases:
        preBuild:
          commands:
            # Next.js 16 needs Node >= 20.9
            - nvm use 20 || nvm install 20
            - npm ci
        build:
          commands:
            # Amplify exposes env vars at build time only; persist the ones SSR
            # needs at runtime (BACKEND_URL, NEXT_PUBLIC_*) into .env.production.
            - env | grep -e ^BACKEND_URL= -e ^NEXT_PUBLIC_ >> .env.production || true
            - npm run build
      artifacts:
        baseDirectory: .next
        files:
          - '**/*'
      cache:
        paths:
          - node_modules/**/*
          - .next/cache/**/*
  YAML

  # BACKEND_URL is the hook for FOOD-48: next.config.ts should read it as the
  # /api rewrite destination instead of the hard-coded localhost:8000.
  #
  # It is set on the *branch*, not the app, on purpose: the App Runner service
  # reads this app's default domain for CORS_ORIGINS / FRONTEND_URL (FOOD-50),
  # and the App Runner URL flows back here. Keeping aws_amplify_app free of any
  # backend reference makes that app -> App Runner -> branch chain acyclic.
  branch_environment_variables = merge(
    var.backend_url == null ? {} : { BACKEND_URL = var.backend_url },
    var.branch_environment_variables,
  )

  branch_url = "https://${var.branch_name}.${aws_amplify_app.this.default_domain}"
  custom_url = var.custom_domain == null ? null : (
    var.custom_domain_prefix == "" ? "https://${var.custom_domain}" : "https://${var.custom_domain_prefix}.${var.custom_domain}"
  )
}

# ---------------------------------------------------------------------------
# Service role: Amplify Hosting compute (Next.js SSR) writes its logs here.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["amplify.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "logs" {
  statement {
    sid    = "AmplifySSRLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DescribeLogGroups",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:logs:*:*:log-group:/aws/amplify/*"]
  }
}

data "aws_partition" "current" {}

resource "aws_iam_role" "amplify" {
  name               = "${var.name}-amplify"
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = var.tags
}

resource "aws_iam_role_policy" "amplify_logs" {
  name   = "ssr-logs"
  role   = aws_iam_role.amplify.id
  policy = data.aws_iam_policy_document.logs.json
}

# ---------------------------------------------------------------------------
# App + production branch
# ---------------------------------------------------------------------------

resource "aws_amplify_app" "this" {
  name        = var.name
  description = "Food - Next.js frontend"

  repository   = var.repository
  access_token = var.github_access_token

  platform             = "WEB_COMPUTE"
  build_spec           = coalesce(var.build_spec, local.default_build_spec)
  iam_service_role_arn = aws_iam_role.amplify.arn

  enable_branch_auto_build    = false
  enable_branch_auto_deletion = false
  enable_auto_branch_creation = false

  environment_variables = var.environment_variables

  # Optional FOOD-48 path: proxy /api/* at the Amplify edge to the API.
  # A "200" status makes Amplify reverse-proxy rather than redirect. The target
  # must be known before apply (the API custom domain) - see the note above.
  dynamic "custom_rule" {
    for_each = var.api_rewrite_target == null ? [] : [var.api_rewrite_target]
    content {
      source = "/api/<*>"
      target = "${custom_rule.value}/api/<*>"
      status = "200"
    }
  }

  tags = var.tags

  lifecycle {
    # The token is only used to install the GitHub connection; Amplify does
    # not echo it back, so ignore it to prevent perpetual diffs.
    ignore_changes = [access_token, oauth_token]
  }
}

resource "aws_amplify_branch" "production" {
  app_id      = aws_amplify_app.this.id
  branch_name = var.branch_name

  framework         = "Next.js - SSR"
  stage             = "PRODUCTION"
  enable_auto_build = var.enable_auto_build

  environment_variables = local.branch_environment_variables

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Optional custom domain
# ---------------------------------------------------------------------------

resource "aws_amplify_domain_association" "this" {
  count = var.custom_domain == null ? 0 : 1

  app_id                = aws_amplify_app.this.id
  domain_name           = var.custom_domain
  wait_for_verification = false

  sub_domain {
    branch_name = aws_amplify_branch.production.branch_name
    prefix      = var.custom_domain_prefix
  }
}
