variable "name" {
  description = "Name prefix."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the DB subnet group (min 2 AZs)."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group that only admits 5432 from App Runner."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL major version. A bare major (\"16\") lets RDS pick the latest minor and auto-upgrade."
  type        = string
  default     = "16"

  validation {
    condition     = startswith(var.engine_version, "16")
    error_message = "The Food app targets PostgreSQL 16."
  }
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "Initial storage (GiB)."
  type        = number
  default     = 20
}

variable "max_allocated_storage" {
  description = "Storage autoscaling ceiling (GiB). 0 disables autoscaling."
  type        = number
  default     = 100
}

variable "database_name" {
  type    = string
  default = "food"
}

variable "master_username" {
  type    = string
  default = "food_admin"
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "skip_final_snapshot" {
  description = "Set true only for throwaway environments."
  type        = bool
  default     = false
}

variable "apply_immediately" {
  type    = bool
  default = false
}

variable "performance_insights_enabled" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
