locals {
  name          = "${var.project}-${var.environment}"
  secret_prefix = "${var.project}/${var.environment}"

  tags = merge({
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repository  = var.github_repository
  }, var.tags)

  # ---- FOOD-50: frontend <-> backend URL wiring -------------------------
  #
  # The Amplify app's default domain (https://<branch>.<app-id>.amplifyapp.com)
  # is read straight from the Amplify module and fed into App Runner's
  # CORS_ORIGINS / FRONTEND_URL. The App Runner URL flows back to Amplify as a
  # *branch* env var (BACKEND_URL), so the dependency chain is
  # aws_amplify_app -> aws_apprunner_service -> aws_amplify_branch (no cycle).
  custom_frontend_url = var.frontend_custom_domain == null ? null : (
    var.frontend_custom_domain_prefix == "" ? "https://${var.frontend_custom_domain}" : "https://${var.frontend_custom_domain_prefix}.${var.frontend_custom_domain}"
  )
  amplify_branch_url = try(module.amplify[0].branch_url, null)

  # FRONTEND_URL: explicit override > custom domain > Amplify default domain.
  frontend_url = try(coalesce(var.frontend_url, local.custom_frontend_url, local.amplify_branch_url), null)

  # CORS_ORIGINS: every origin the browser may load the app from - the Amplify
  # default domain, the custom domain when set, the override, plus extras.
  cors_origins = distinct(compact(concat(
    [local.amplify_branch_url, local.custom_frontend_url, var.frontend_url],
    var.additional_cors_origins,
  )))

  # Plan-known: is at least one CORS origin going to exist?
  cors_enabled = var.create_amplify_app || var.frontend_custom_domain != null || var.frontend_url != null || length(var.additional_cors_origins) > 0

  # Prefer the API custom domain (known before apply); fall back to the
  # generated *.awsapprunner.com URL.
  backend_url = var.backend_custom_domain != null ? "https://${var.backend_custom_domain}" : module.app_runner.service_url
}

# ---------------------------------------------------------------------------
# 1. Network
# ---------------------------------------------------------------------------

module "network" {
  source = "./modules/network"

  name               = local.name
  region             = var.region
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  enable_nat_gateway = true
  single_nat_gateway = var.single_nat_gateway
  tags               = local.tags
}

# ---------------------------------------------------------------------------
# 2. RDS PostgreSQL 16 (private subnets, 5432 from App Runner only)
# ---------------------------------------------------------------------------

module "database" {
  source = "./modules/database"

  name                = local.name
  private_subnet_ids  = module.network.private_subnet_ids
  security_group_id   = module.network.database_security_group_id
  engine_version      = "16"
  instance_class      = var.db_instance_class
  allocated_storage   = var.db_allocated_storage
  multi_az            = var.db_multi_az
  deletion_protection = var.db_deletion_protection
  skip_final_snapshot = var.db_skip_final_snapshot
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# 4. Uploads bucket (private; receipts/, meals/, cookbook/)
# ---------------------------------------------------------------------------

module "storage" {
  source = "./modules/storage"

  name                 = local.name
  bucket_name          = var.uploads_bucket_name
  allowed_prefixes     = ["receipts", "meals", "cookbook"]
  force_destroy        = var.uploads_force_destroy
  enable_cors          = local.cors_enabled
  cors_allowed_origins = local.cors_origins
  tags                 = local.tags
}

# ---------------------------------------------------------------------------
# 5. Secrets Manager
#
# database_url : composed from the RDS endpoint + generated password (managed)
# auth_secret  : 64-char random string generated once by Terraform
# anthropic / openai / resend : "REPLACE_ME" placeholders - set real values
#                               with `aws secretsmanager put-secret-value`
# ---------------------------------------------------------------------------

resource "random_password" "auth_secret" {
  length  = 64
  special = false
}

module "secrets" {
  source = "./modules/secrets"

  name_prefix             = local.secret_prefix
  recovery_window_in_days = var.secrets_recovery_window_in_days
  tags                    = local.tags

  secrets = {
    database_url = { description = "SQLAlchemy URL for the ${local.name} RDS instance (Terraform-managed)", managed = true }
    auth_secret  = { description = "AUTH_SECRET - peppers session and password-reset tokens" }
    anthropic    = { description = "ANTHROPIC_API_KEY for receipt parsing and meal generation" }
    openai       = { description = "OPENAI_API_KEY for meal image generation" }
    resend       = { description = "RESEND_API_KEY for password-reset email" }
  }

  secret_values = {
    database_url = module.database.database_url
    auth_secret  = random_password.auth_secret.result
    anthropic    = "REPLACE_ME"
    openai       = "REPLACE_ME"
    resend       = "REPLACE_ME"
  }
}

# ---------------------------------------------------------------------------
# 3. App Runner (FastAPI)
# ---------------------------------------------------------------------------

module "app_runner" {
  source = "./modules/app_runner"

  name             = "${local.name}-api"
  create_service   = var.create_app_runner_service
  image_identifier = var.backend_image_identifier
  image_tag        = var.backend_image_tag
  cpu              = var.backend_cpu
  memory           = var.backend_memory
  min_size         = var.backend_min_size
  max_size         = var.backend_max_size

  health_check_path = "/api/health"
  custom_domain     = var.backend_custom_domain

  private_subnet_ids = module.network.private_subnet_ids
  security_group_id  = module.network.app_runner_security_group_id

  # FOOD-50 production env. Names follow backend/app/config.py
  # (pydantic-settings, case-insensitive).
  environment_variables = merge(
    {
      # Settings.session_cookie_secure is true when environment == "production";
      # COOKIE_SECURE=true is the explicit belt-and-braces flag the app also reads.
      ENVIRONMENT    = "production"
      COOKIE_SECURE  = "true"
      EMAIL_FROM     = var.email_from
      UPLOADS_BUCKET = module.storage.bucket_name # FOOD-47 hook; AWS_REGION is injected by App Runner
    },
    local.frontend_url == null ? {} : { FRONTEND_URL = local.frontend_url },
    length(local.cors_origins) == 0 ? {} : { CORS_ORIGINS = join(",", local.cors_origins) },
    var.backend_extra_environment,
  )

  runtime_secrets = {
    DATABASE_URL      = module.secrets.secret_arns["database_url"]
    AUTH_SECRET       = module.secrets.secret_arns["auth_secret"]
    ANTHROPIC_API_KEY = module.secrets.secret_arns["anthropic"]
    OPENAI_API_KEY    = module.secrets.secret_arns["openai"]
    RESEND_API_KEY    = module.secrets.secret_arns["resend"]
  }

  secret_arns        = values(module.secrets.secret_arns)
  uploads_policy_arn = module.storage.app_access_policy_arn
  tags               = local.tags

  # App Runner fails to start if a referenced secret has no value yet, so wait
  # for the secret *versions*, not just the secret containers.
  depends_on = [module.secrets]
}

# ---------------------------------------------------------------------------
# 6. Amplify Hosting (Next.js)
# ---------------------------------------------------------------------------

module "amplify" {
  source = "./modules/amplify"
  count  = var.create_amplify_app ? 1 : 0

  name                = "${local.name}-web"
  repository          = var.github_repository
  github_access_token = var.github_access_token
  branch_name         = var.amplify_branch

  backend_url        = local.backend_url
  api_rewrite_target = var.amplify_enable_api_rewrite ? "https://${var.backend_custom_domain}" : null

  environment_variables        = var.amplify_environment_variables
  branch_environment_variables = var.amplify_branch_environment_variables

  custom_domain        = var.frontend_custom_domain
  custom_domain_prefix = var.frontend_custom_domain_prefix
  tags                 = local.tags
}
