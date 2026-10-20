#!/usr/bin/env bash
# Delete everything deploy.sh created (stack, tables, functions, API, topic, logs, artifact bucket).
set -euo pipefail

STACK_NAME="${STACK_NAME:-smart-campus-energy}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
REGION="${REGION:-ap-south-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${ARTIFACT_BUCKET:-smart-campus-artifacts-${ACCOUNT_ID}-${REGION}}"

read -r -p "Delete stack '${STACK_NAME}' and bucket '${BUCKET}' in ${REGION}? All data is lost. [y/N] " ok
[ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Cancelled."; exit 0; }

aws cloudformation delete-stack --stack-name "$STACK_NAME"
echo "==> Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
aws s3 rb "s3://${BUCKET}" --force >/dev/null 2>&1 || true
echo "==> Done. (IoT things/certificates created with iot/create-iot-device.sh are not removed automatically.)"
