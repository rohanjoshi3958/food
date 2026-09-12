data "aws_caller_identity" "current" {}

locals {
  bucket_name = coalesce(var.bucket_name, "${var.name}-uploads-${data.aws_caller_identity.current.account_id}")
}

resource "aws_s3_bucket" "uploads" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy

  tags = merge(var.tags, { Name = local.bucket_name })
}

# Block every form of public access and disable ACLs entirely.
resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }

  depends_on = [aws_s3_bucket_public_access_block.uploads]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = var.versioning_enabled ? "Enabled" : "Suspended"
  }
}

# Browser-side uploads via presigned URLs (FOOD-47) need CORS from the
# frontend origin. `enable_cors` must be plan-known (the origins themselves may
# come from resources created in the same apply, e.g. the Amplify domain).
resource "aws_s3_bucket_cors_configuration" "uploads" {
  count = var.enable_cors ? 1 : 0

  bucket = aws_s3_bucket.uploads.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST", "HEAD"]
    allowed_origins = var.cors_allowed_origins
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.uploads.arn,
      "${aws_s3_bucket.uploads.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  policy = data.aws_iam_policy_document.bucket.json

  depends_on = [aws_s3_bucket_public_access_block.uploads]
}

# ---------------------------------------------------------------------------
# IAM policy the backend assumes: object access limited to the upload
# prefixes, and listing limited to those same prefixes.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "app_access" {
  statement {
    sid    = "UploadPrefixObjectAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = [for p in var.allowed_prefixes : "${aws_s3_bucket.uploads.arn}/${p}/*"]
  }

  statement {
    sid       = "ListUploadPrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = flatten([for p in var.allowed_prefixes : ["${p}/", "${p}/*"]])
    }
  }

  statement {
    sid       = "BucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.uploads.arn]
  }
}

resource "aws_iam_policy" "app_access" {
  name        = "${var.name}-uploads-access"
  description = "Read/write ${join(", ", var.allowed_prefixes)} prefixes in ${local.bucket_name}"
  policy      = data.aws_iam_policy_document.app_access.json

  tags = var.tags
}
