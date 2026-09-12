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
  description = "App Runner base URL (no trailing slash). Exposed to the build as BACKEND_URL."
  type        = string
  default     = null
}

variable "enable_api_rewrite" {
  description = "Add an Amplify-edge 200 rewrite for /api/<*> -> backend_url. See README (FOOD-48) before enabling."
  type        = bool
  default     = false
}

variable "environment_variables" {
  description = "App-level environment variables (available at build and to SSR)."
  type        = map(string)
  default     = {}
}

variable "branch_environment_variables" {
  description = "Branch-level overrides."
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
