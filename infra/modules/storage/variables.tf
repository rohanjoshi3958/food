variable "name" {
  description = "Name prefix."
  type        = string
}

variable "bucket_name" {
  description = "Explicit bucket name. Defaults to <name>-uploads-<account-id>."
  type        = string
  default     = null
}

variable "allowed_prefixes" {
  description = "Key prefixes the backend may read/write/list."
  type        = list(string)
  default     = ["receipts", "meals", "cookbook"]

  validation {
    condition     = alltrue([for p in var.allowed_prefixes : !endswith(p, "/") && !startswith(p, "/")])
    error_message = "Prefixes must not have leading or trailing slashes."
  }
}

variable "versioning_enabled" {
  type    = bool
  default = true
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty bucket."
  type        = bool
  default     = false
}

variable "enable_cors" {
  description = "Create the bucket CORS configuration. Must be known at plan time; cors_allowed_origins must be non-empty when true."
  type        = bool
  default     = false
}

variable "cors_allowed_origins" {
  description = "Origins allowed to PUT/GET objects directly (presigned URLs). May contain apply-time values."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
