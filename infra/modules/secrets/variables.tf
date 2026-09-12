variable "name_prefix" {
  description = "Secret name prefix, e.g. food/prod -> food/prod/<key>."
  type        = string
}

variable "secrets" {
  description = <<-EOT
    Secrets to create, keyed by the final path segment.
    - managed = true : Terraform owns the value and reconciles it every apply.
    - managed = false: Terraform writes the initial value once, then ignores
                       changes so operators can rotate it out-of-band.
  EOT
  type = map(object({
    description = string
    managed     = optional(bool, false)
  }))
}

variable "secret_values" {
  description = "Initial value per key in `secrets` (same keys). Kept separate so for_each can iterate the non-sensitive metadata."
  type        = map(string)
  sensitive   = true

  validation {
    condition     = length(setsubtract(keys(var.secret_values), keys(var.secrets))) == 0
    error_message = "secret_values contains keys that are not declared in secrets."
  }
}

variable "kms_key_id" {
  description = "Optional CMK. Defaults to the AWS-managed aws/secretsmanager key."
  type        = string
  default     = null
}

variable "recovery_window_in_days" {
  description = "Days before a deleted secret is purged (0 = immediate, for throwaway envs)."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
