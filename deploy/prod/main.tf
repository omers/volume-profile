# Production deployment of the frvp executor (deploy/terraform module).
#
#   deploy/build.sh
#   export TF_VAR_alpaca_api_key_id=...  TF_VAR_alpaca_secret_key=...
#   terraform -chdir=deploy/prod init
#   terraform -chdir=deploy/prod apply
#
# State is local (deploy/prod/terraform.tfstate) and holds the Alpaca keys in plain text: keep it private.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "volume-profile"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

# Alpaca keys: set them in the shell (TF_VAR_...), never in a file
variable "alpaca_api_key_id" {
  description = "Alpaca API key ID"
  type        = string
  sensitive   = true
}

variable "alpaca_secret_key" {
  description = "Alpaca secret key"
  type        = string
  sensitive   = true
}

# Trade notification address: set it in terraform.tfvars or TF_VAR_notify_email, not here (the repo is public)
variable "notify_email" {
  description = "Email address subscribed to the executor's trade notifications; empty for none"
  type        = string
  default     = ""
}

module "executor" {
  source = "../terraform"

  name              = "frvp-executor"
  alpaca_api_key_id = var.alpaca_api_key_id
  alpaca_secret_key = var.alpaca_secret_key

  # Paper account and dry run first; turn off dry_run, then switch to live keys, once the logs look right
  alpaca_paper  = true
  dry_run       = false
  enabled       = true
  schedule      = "cron(0/15 9-15 ? * MON-FRI *)" # every 15 min, 9:00-15:45 ET on weekdays (America/New_York); the 9:00 and 9:15 runs find the market closed and do nothing; 15:45 is the pre-close run
  ticker        = "SPY"
  profile_start = "2026-03-30"
  allocation    = 0.98
  t1_fraction   = 0.5
  min_t1_pct    = 1.0
  notify_email  = var.notify_email
}

output "function_name" {
  value = module.executor.function_name
}

output "log_group" {
  value = module.executor.log_group
}

output "test_command" {
  description = "Runs the function once now, as a dry run that treats the market as open"
  value       = module.executor.test_command
}
