output "function_name" {
  value = aws_lambda_function.executor.function_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.lambda.name
}

output "test_command" {
  description = "Runs the function once now, as a dry run that treats the market as open"
  value       = "aws lambda invoke --region ${data.aws_region.current.region} --function-name ${aws_lambda_function.executor.function_name} --cli-binary-format raw-in-base64-out --payload '{\"assume_open\": true}' /dev/stdout"
}
