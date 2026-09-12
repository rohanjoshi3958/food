# ---------------------------------------------------------------------------
# Global
# ---------------------------------------------------------------------------

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project slug used in resource names and secret paths."
  type        = string
  default     = "food"
}

variable "environment" {
  description = "Environment slug (prod, staging). Secrets are named <project>/<environment>/<key>."
  type        = string
  default     = "prod"
}

variable "tags" {
  description = "Extra tags merged onto every resource."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  type    = number
  default = 2
}

variable "single_nat_gateway" {
  description = "One shared NAT gateway (cheaper) instead of one per AZ."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "db_skip_final_snapshot" {
  type    = bool
  default = false
}

# ---------------------------------------------------------------------------
# Backend (App Runner)
# ---------------------------------------------------------------------------

variable "create_app_runner_service" {
  description = "Set false for the very first apply (before an image exists in ECR), then true."
  type        = bool
  default     = true
}

variable "backend_image_identifier" {
  description = "Override the container image URI. Defaults to the Terraform-managed ECR repo + backend_image_tag."
  type        = string
  default     = null
}

variable "backend_image_tag" {
  type    = string
  default = "latest"
}

variable "backend_cpu" {
  type    = string
  default = "1024"
}

variable "backend_memory" {
  type    = string
  default = "2048"
}

variable "backend_min_size" {
  type    = number
  default = 1
}

variable "backend_max_size" {
  type    = number
  default = 3
}

variable "backend_custom_domain" {
  description = "Optional FQDN for the API (e.g. api.example.com). Known up front, so Amplify's BACKEND_URL does not need a second apply."
  type        = string
  default     = null
}

variable "backend_extra_environment" {
  description = "Additional plain-text env vars for FastAPI (merged over the defaults)."
  type        = map(string)
  default     = {}
}

variable "email_from" {
  description = "EMAIL_FROM for Resend password-reset mail."
  type        = string
  default     = "Food <onboarding@resend.dev>"
}

# FOOD-50: CORS / FRONTEND_URL wiring. The Amplify default domain is only known
# after the first apply, so either set these explicitly or use a custom domain.
variable "frontend_url" {
  description = "Public URL of the Next.js frontend (used for FRONTEND_URL and as the default CORS origin). Null = derive from the Amplify custom domain if set."
  type        = string
  default     = null
}

variable "cors_origins" {
  description = "Explicit CORS_ORIGINS list. Empty = [frontend_url] when known."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Frontend (Amplify)
# ---------------------------------------------------------------------------

variable "create_amplify_app" {
  type    = bool
  default = true
}

variable "github_repository" {
  type    = string
  default = "https://github.com/rohanjoshi3958/food"
}

variable "github_access_token" {
  description = "GitHub PAT for the initial Amplify <-> GitHub connection. Supply via TF_VAR_github_access_token."
  type        = string
  sensitive   = true
  default     = null
}

variable "amplify_branch" {
  type    = string
  default = "main"
}

variable "amplify_enable_api_rewrite" {
  description = "FOOD-48 option B: Amplify-edge 200 rewrite of /api/<*> to App Runner. Read the README caveats first."
  type        = bool
  default     = false
}

variable "amplify_environment_variables" {
  description = "Extra Amplify app env vars (build + SSR)."
  type        = map(string)
  default     = {}
}

variable "frontend_custom_domain" {
  description = "Optional root domain for Amplify (e.g. example.com)."
  type        = string
  default     = null
}

variable "frontend_custom_domain_prefix" {
  description = "Subdomain for the production branch on frontend_custom_domain (\"\" = apex)."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Storage / secrets
# ---------------------------------------------------------------------------

variable "uploads_bucket_name" {
  description = "Explicit S3 bucket name. Null = <project>-<environment>-uploads-<account-id>."
  type        = string
  default     = null
}

variable "uploads_force_destroy" {
  type    = bool
  default = false
}

variable "secrets_recovery_window_in_days" {
  type    = number
  default = 30
}
