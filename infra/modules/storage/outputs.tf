output "bucket_name" {
  value = aws_s3_bucket.uploads.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.uploads.arn
}

output "bucket_regional_domain_name" {
  value = aws_s3_bucket.uploads.bucket_regional_domain_name
}

output "app_access_policy_arn" {
  description = "Attach to the App Runner instance role."
  value       = aws_iam_policy.app_access.arn
}

output "allowed_prefixes" {
  value = var.allowed_prefixes
}
