locals {
  managed     = { for k, v in var.secrets : k => v if v.managed }
  placeholder = { for k, v in var.secrets : k => v if !v.managed }
}

resource "aws_secretsmanager_secret" "this" {
  for_each = var.secrets

  name                    = "${var.name_prefix}/${each.key}"
  description             = each.value.description
  kms_key_id              = var.kms_key_id
  recovery_window_in_days = var.recovery_window_in_days

  tags = merge(var.tags, { Name = "${var.name_prefix}/${each.key}" })
}

# Values Terraform owns (derived from other resources, e.g. the RDS URL).
# Terraform keeps these in sync on every apply.
resource "aws_secretsmanager_secret_version" "managed" {
  for_each = local.managed

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = var.secret_values[each.key]
}

# Operator-owned credentials. Terraform seeds an initial value once so App
# Runner can resolve the secret at boot; the real value is written out-of-band
# with `aws secretsmanager put-secret-value` and Terraform never overwrites it.
resource "aws_secretsmanager_secret_version" "placeholder" {
  for_each = local.placeholder

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = var.secret_values[each.key]

  lifecycle {
    ignore_changes = [secret_string, version_stages]
  }
}
