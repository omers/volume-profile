variable "region" {
  description = "AWS region for the function"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name of the Lambda function and the resources around it"
  type        = string
  default     = "frvp-executor"
}

# Alpaca keys: set them in the shell, never in a file:
#   export TF_VAR_alpaca_api_key_id=...  TF_VAR_alpaca_secret_key=...
variable "alpaca_api_key_id" {
  description = "Alpaca API key ID (ALPACA_API_KEY_ID)"
  type        = string
  sensitive   = true
}

variable "alpaca_secret_key" {
  description = "Alpaca secret key (ALPACA_SECRET_KEY)"
  type        = string
  sensitive   = true
}

variable "alpaca_paper" {
  description = "true = Alpaca paper account; false = live trading (needs live keys)"
  type        = bool
  default     = true
}

variable "dry_run" {
  description = "true = log the orders without sending them"
  type        = bool
  default     = true
}

variable "schedule" {
  description = "When the executor runs, in America/New_York time. Default: 9:45 to 15:45 on weekdays, hourly; the run within pre_close_minutes of the close acts on the day's close"
  type        = string
  default     = "cron(45 9-15 ? * MON-FRI *)"
}

variable "enabled" {
  description = "false pauses the schedule"
  type        = bool
  default     = true
}

variable "ticker" {
  description = "Chart the model runs on"
  type        = string
  default     = "SPY"
}

variable "profile_start" {
  description = "Profile anchor date (the model's --start)"
  type        = string
  default     = "2026-03-30"
}

variable "allocation" {
  description = "Share of cash put into the ETF on an entry (HOWTO: all in; a little below 1 leaves room for the price moving)"
  type        = number
  default     = 0.98
}

variable "t1_fraction" {
  description = "Share of the position sold at T1; the rest is held for T2"
  type        = number
  default     = 0.5
}

variable "min_t1_pct" {
  description = "Skip a signal whose T1 is less than this % from its entry (HOWTO: about 1%); 0 turns it off"
  type        = number
  default     = 1.0
}

variable "pre_close_minutes" {
  description = "A run this many minutes or less before the close acts on the day's close (entries on rules 1-6, stop on close, time stop)"
  type        = number
  default     = 20
}

variable "notify_email" {
  description = "Email that gets an SNS message for every order and failure (confirm the subscription email); empty = no notifications"
  type        = string
  default     = ""
}

variable "package_dir" {
  description = "Folder built by deploy/build.sh"
  type        = string
  default     = "../build/package"
}
