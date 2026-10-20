#!/usr/bin/env bash
# Print verification evidence for every AWS service in the deployed stack.
# Run in AWS CloudShell from the repo folder:
#   bash tools/verify.sh            # everything
#   bash tools/verify.sh 5          # one exercise (1-10), or: metrics
set -uo pipefail
STACK="${STACK_NAME:-smart-campus-energy}"
export AWS_PAGER=""

out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
res() { aws cloudformation describe-stack-resource --stack-name "$STACK" --logical-resource-id "$1" --query StackResourceDetail.PhysicalResourceId --output text; }
section() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$1"; }
run() { printf '\033[0;33m$ %s\033[0m\n' "$*"; eval "$@"; }

ex1() {
  section "Exercise 1 - AWS CloudShell and AWS CLI"
  run aws --version
  run aws sts get-caller-identity --output table
  run 'echo "Region: $AWS_REGION"'
}
ex2() {
  section "Exercise 2 - Amazon S3 (private deployment-artifact bucket)"
  local b; b="$(aws s3 ls | awk '/smart-campus-artifacts/{print $3}' | head -1)"
  run aws s3 ls
  run aws s3 ls "s3://$b/$STACK/" --human-readable
  run aws s3api get-public-access-block --bucket "$b" --output table
}
ex3() {
  section "Exercise 3 - IAM least-privilege execution roles"
  run "aws iam list-roles --query \"Roles[?starts_with(RoleName,'$STACK')].[RoleName,CreateDate]\" --output table"
  local r; r="$(aws iam list-roles --query "Roles[?starts_with(RoleName,'$STACK-IngestFunctionRole')].RoleName" --output text)"
  run aws iam list-attached-role-policies --role-name "$r" --output table
  run aws iam list-role-policies --role-name "$r" --output table
}
ex4() {
  section "Exercise 4 - AWS CloudFormation / SAM (infrastructure as code)"
  run "aws cloudformation describe-stacks --stack-name $STACK --query 'Stacks[0].[StackName,StackStatus,LastUpdatedTime]' --output table"
  run "aws cloudformation list-stack-resources --stack-name $STACK --query 'StackResourceSummaries[].[LogicalResourceId,ResourceType,ResourceStatus]' --output table"
}
ex5() {
  section "Exercise 5 - Amazon DynamoDB (on-demand tables with TTL)"
  run "aws dynamodb list-tables --query \"TableNames[?starts_with(@,'$STACK')]\" --output table"
  local t; t="$(res ReadingsTable)"
  run aws dynamodb describe-time-to-live --table-name "$t" --output table
  run "aws dynamodb describe-table --table-name $t --query 'Table.[TableName,BillingModeSummary.BillingMode,ItemCount,KeySchema[0].AttributeName,KeySchema[1].AttributeName]' --output table"
  run "aws dynamodb query --table-name $(res RoomsTable) --key-condition-expression 'room_id = :r' --expression-attribute-values '{\":r\":{\"S\":\"LAB-CS1\"}}' --projection-expression 'room_id,#s,last_ts,waste_streak' --expression-attribute-names '{\"#s\":\"status\"}' --output table"
}
ex6() {
  section "Exercise 6 - AWS Lambda (serverless functions)"
  run "aws lambda list-functions --query \"Functions[?starts_with(FunctionName,'$STACK')].[FunctionName,Runtime,Architectures[0],MemorySize,Timeout]\" --output table"
  run "aws lambda invoke --function-name $(out SimulatorFunctionName) --cli-binary-format raw-in-base64-out --payload '{\"mode\":\"inject\",\"scenario\":\"waste\",\"room_id\":\"AB1-101\"}' /tmp/inject.json --query StatusCode"
  run "python3 -m json.tool /tmp/inject.json"
}
ex7() {
  section "Exercise 7 - Amazon API Gateway (HTTP API, CORS, throttling)"
  run "aws apigatewayv2 get-apis --query \"Items[].[Name,ProtocolType,ApiEndpoint,CorsConfiguration.AllowOrigins[0]]\" --output table"
  run "aws apigatewayv2 get-routes --api-id $(out ApiUrl | cut -d/ -f3 | cut -d. -f1) --query 'Items[].RouteKey' --output table"
  run "curl -s $(out ApiUrl)/summary | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps(d[\"campus\"], indent=1))'"
}
ex8() {
  section "Exercise 8 - Amazon EventBridge (scheduled automation)"
  run "aws events list-rules --query \"Rules[?starts_with(Name,'$STACK')].[Name,ScheduleExpression,State]\" --output table"
  run "curl -s '$(out ApiUrl)/rooms/LAB-CS1/readings?hours=1' | python3 -c 'import sys,json,time; [print(time.strftime(\"%H:%M:%S\", time.gmtime(r[\"ts\"]+19800)), \"IST\", r[\"power_w\"], \"W\", r[\"status\"]) for r in json.load(sys.stdin)[\"readings\"][-6:]]'"
}
ex9() {
  section "Exercise 9 - Amazon SNS alerts + Amazon CloudWatch logs and alarms"
  run "aws sns get-topic-attributes --topic-arn $(out AlertTopicArn) --query 'Attributes.[DisplayName,SubscriptionsConfirmed,TopicArn]' --output table"
  run "aws cloudwatch describe-alarms --query \"MetricAlarms[?starts_with(AlarmName,'$STACK')].[AlarmName,MetricName,Threshold,StateValue]\" --output table"
  run "aws logs tail $(res SimulatorLogGroup) --since 15m --format short | grep -v -E '^\s*$' | tail -n 8 | cut -c1-200"
}
ex10() {
  section "Exercise 10 - AWS IoT Core (MQTT topic rule)"
  local ep; ep="$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)"
  run "aws iot list-topic-rules --query 'rules[].[ruleName,topicPattern,ruleDisabled]' --output table"
  run "aws iot-data publish --endpoint-url https://$ep --topic campus/energy/IOT-LAB-2/readings --cli-binary-format raw-in-base64-out --payload '{\"power_w\": 4200, \"voltage_v\": 231, \"occupancy\": 0}'"
  sleep 3
  run "curl -s '$(out ApiUrl)/rooms/IOT-LAB-2/readings?hours=1' | python3 -m json.tool | head -20"
}
metrics() {
  section "Lambda performance (CloudWatch metrics, last 3 hours)"
  local start end
  start="$(date -u -d '-3 hours' +%FT%TZ)"; end="$(date -u +%FT%TZ)"
  printf '%-22s %8s %10s %10s %7s\n' function invocations avg_ms max_ms errors
  for logical in IngestFunction ApiFunction SimulatorFunction ForecastFunction; do
    local fn; fn="$(res "$logical")"
    stat() { aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name "$1" --dimensions Name=FunctionName,Value="$fn" \
      --start-time "$start" --end-time "$end" --period 10800 --statistics "$2" --query "Datapoints[0].$2" --output text; }
    printf '%-22s %8s %10.0f %10.0f %7s\n' "$logical" "$(stat Invocations Sum)" "$(stat Duration Average | sed 's/None/0/')" \
      "$(stat Duration Maximum | sed 's/None/0/')" "$(stat Errors Sum)"
  done
}

case "${1:-all}" in
  all) ex1; ex2; ex3; ex4; ex5; ex6; ex7; ex8; ex9; ex10; metrics ;;
  metrics) metrics ;;
  [1-9]|10) "ex$1" ;;
  *) echo "usage: $0 [1-10|metrics|all]"; exit 1 ;;
esac
