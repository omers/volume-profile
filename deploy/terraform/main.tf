data "aws_caller_identity" "me" {}

# ───── Code: deploy/build.sh fills package_dir; the zip (~60 MB) is too big to upload directly, so it goes through S3
data "archive_file" "package" {
  type        = "zip"
  source_dir  = var.package_dir
  output_path = "${path.module}/../build/${var.name}.zip"
}

resource "aws_s3_bucket" "code" {
  bucket        = "${var.name}-code-${data.aws_caller_identity.me.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "code" {
  bucket                  = aws_s3_bucket.code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "package" {
  bucket      = aws_s3_bucket.code.id
  key         = "${var.name}.zip"
  source      = data.archive_file.package.output_path
  source_hash = data.archive_file.package.output_base64sha256
}

# ───── Function
resource "aws_iam_role" "lambda" {
  name = "${var.name}-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "notify" {
  count = var.notify_email == "" ? 0 : 1
  name  = "sns-publish"
  role  = aws_iam_role.lambda.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = aws_sns_topic.notify[0].arn }]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 90
}

resource "aws_lambda_function" "executor" {
  function_name    = var.name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "executor.handler"
  s3_bucket        = aws_s3_object.package.bucket
  s3_key           = aws_s3_object.package.key
  source_code_hash = data.archive_file.package.output_base64sha256
  memory_size      = 1024
  timeout          = 180

  environment {
    variables = {
      ALPACA_API_KEY_ID = var.alpaca_api_key_id
      ALPACA_SECRET_KEY = var.alpaca_secret_key
      ALPACA_PAPER      = tostring(var.alpaca_paper)
      DRY_RUN           = tostring(var.dry_run)
      TICKER            = var.ticker
      PROFILE_START     = var.profile_start
      ALLOCATION        = tostring(var.allocation)
      T1_FRACTION       = tostring(var.t1_fraction)
      MIN_T1_PCT        = tostring(var.min_t1_pct)
      PRE_CLOSE_MINUTES = tostring(var.pre_close_minutes)
      NOTIFY_TOPIC_ARN  = var.notify_email == "" ? "" : aws_sns_topic.notify[0].arn
      XDG_CACHE_HOME    = "/tmp"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.logs]
}

# A failed run is not retried: the next scheduled run reconciles anyway
resource "aws_lambda_function_event_invoke_config" "executor" {
  function_name          = aws_lambda_function.executor.function_name
  maximum_retry_attempts = 0
}

# ───── Schedule (EventBridge Scheduler, New York time so it follows daylight saving)
resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-executor"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.executor.arn }]
  })
}

resource "aws_scheduler_schedule" "executor" {
  name                         = var.name
  state                        = var.enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.schedule
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.executor.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

# ───── Notifications (optional)
resource "aws_sns_topic" "notify" {
  count = var.notify_email == "" ? 0 : 1
  name  = var.name
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.notify_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.notify[0].arn
  protocol  = "email"
  endpoint  = var.notify_email
}
