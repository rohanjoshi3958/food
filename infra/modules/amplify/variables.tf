variable "name" {
  description = "Amplify app name."
  type        = string
}

variable "repository" {
  description = "GitHub repository URL for the Next.js app."
  type        = string
}

variable "github_access_token" {
  description = <<-EOT
    GitHub personal access token used once to connect the repository
    (requires the Amplify GitHub App to be installed on the repo first).
    Pass via TF_VAR_github_access_token; never commit it.
  EOT
  type        = string
  sensitive   = true
  default     = null
}

variable "branch_name" {
  type    = string
  default = "main"
}

variable "enable_auto_build" {
  description = "Build and deploy on every push to branch_name."
  type        = bool
  default     = true
}

variable "build_spec" {
  description = "Override the default Next.js build spec."
  type        = string
  default     = null
}

variable "backend_url" {
  description = "API base URL (no trailing slash), exposed to the production branch as BACKEND_URL. May depend on App Runner."
  type        = string
  default     = null
}

variable "api_rewrite_target" {
  description = "Statically known API base URL for an Amplify-edge 200 rewrite of /api/<*>. Null disables the rule. Must NOT be derived from App Runner (cycle). See README (FOOD-48)."
  type        = string
  default     = null
}

variable "environment_variables" {
  description = "App-level environment variables. Must not reference App Runner outputs (see main.tf)."
  type        = map(string)
  default     = {}
}

variable "branch_environment_variables" {
  description = "Production-branch environment variables (merged over BACKEND_URL)."
  type        = map(string)
  default     = {}
}

variable "custom_domain" {
  description = "Root domain to attach (e.g. example.com). Null skips domain association."
  type        = string
  default     = null
}

variable "custom_domain_prefix" {
  description = "Subdomain prefix for the production branch (\"\" = apex, \"www\" = www.example.com)."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
