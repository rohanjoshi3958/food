variable "name" {
  description = "Service name / prefix."
  type        = string
}

variable "create_service" {
  description = "Create the App Runner service. Set false on the first apply if no image has been pushed to ECR yet."
  type        = bool
  default     = true
}

variable "create_ecr_repository" {
  type    = bool
  default = true
}

variable "ecr_force_delete" {
  description = "Allow destroying the ECR repo while it still holds images."
  type        = bool
  default     = false
}

variable "ecr_images_to_keep" {
  type    = number
  default = 20
}

variable "image_identifier" {
  description = "Full image URI (repo:tag or repo@digest). Defaults to the module-created ECR repo + image_tag."
  type        = string
  default     = null
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "auto_deployments_enabled" {
  description = "Redeploy automatically when a new image is pushed to the tracked tag."
  type        = bool
  default     = true
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "cpu" {
  description = "vCPU units (256 | 512 | 1024 | 2048 | 4096)."
  type        = string
  default     = "1024"
}

variable "memory" {
  description = "Memory in MB (must pair with cpu, e.g. 1024 -> 2048)."
  type        = string
  default     = "2048"
}

variable "min_size" {
  type    = number
  default = 1
}

variable "max_size" {
  type    = number
  default = 3
}

variable "max_concurrency" {
  description = "Concurrent requests per instance before scaling out."
  type        = number
  default     = 50
}

variable "health_check_path" {
  type    = string
  default = "/api/health"
}

variable "health_check_interval" {
  description = "Seconds between health checks (1-20)."
  type        = number
  default     = 10
}

variable "health_check_timeout" {
  description = "Seconds to wait for a health response (1-20)."
  type        = number
  default     = 5
}

variable "custom_domain" {
  description = "Optional FQDN for the API (e.g. api.example.com). You must create the returned DNS records yourself."
  type        = string
  default     = null
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  description = "Security group for the VPC connector ENIs (5432 to RDS + 443 egress)."
  type        = string
}

variable "environment_variables" {
  description = "Plain-text runtime environment variables."
  type        = map(string)
  default     = {}
}

variable "runtime_secrets" {
  description = "Env var name -> Secrets Manager ARN, injected by App Runner at start."
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "Secret ARNs the instance role may read."
  type        = list(string)
}

variable "secrets_kms_key_arn" {
  description = "CMK ARN if secrets use a customer-managed key."
  type        = string
  default     = null
}

variable "uploads_policy_arn" {
  description = "IAM policy granting prefix-scoped access to the uploads bucket."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
