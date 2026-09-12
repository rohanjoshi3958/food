terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is strongly recommended: the state file contains the RDS
  # master password and the composed DATABASE_URL. Copy backend.tf.example to
  # backend.tf and fill in your bucket, or keep local state out of git
  # (see .gitignore).
}
