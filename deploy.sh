#!/usr/bin/env bash
# One-command deployment of the Smart Campus Energy backend to AWS.
#
# Easiest: open AWS CloudShell (already signed in, no access keys needed) and run
#   git clone https://github.com/bedyrune17/smart-campus-energy-management.git
#   cd smart-campus-energy-management && bash deploy.sh
#
# Optional environment variables
#   ALERT_EMAIL=you@example.com  e-mail for high-severity alerts (click the confirmation link AWS sends)
#   AWS_REGION=ap-south-1        region (defaults to the CloudShell / CLI region)
#   STACK_NAME=smart-campus-energy
#   SIMULATOR_ENABLED=false      stop the cloud simulator once real meters are connected
#   BACKFILL=1                   re-seed 14 days of history on an existing stack
#
# Needs only the AWS CLI v2 (no SAM CLI, Docker or pip packages).
set -euo pipefail
cd "$(dirname "$0")"

STACK_NAME="${STACK_NAME:-smart-campus-energy}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
REGION="${REGION:-ap-south-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${ARTIFACT_BUCKET:-smart-campus-artifacts-${ACCOUNT_ID}-${REGION}}"

# GitHub Pages URL of the dashboard, derived from the git remote
DASHBOARD_URL=""
REMOTE="$(git config --get remote.origin.url 2>/dev/null || true)"
if [[ "$REMOTE" =~ github\.com[:/]([^/]+)/([^/]+)$ ]]; then
  OWNER="$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
  REPO="${BASH_REMATCH[2]%.git}"
  DASHBOARD_URL="https://${OWNER}.github.io/${REPO}/"
fi

echo "==> Account ${ACCOUNT_ID} | region ${REGION} | stack ${STACK_NAME}"

FIRST_DEPLOY=false
aws cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1 || FIRST_DEPLOY=true

if ! aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "==> Creating private artifact bucket s3://${BUCKET}"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

echo "==> Packaging Lambda code"
aws cloudformation package --template-file template.yaml --s3-bucket "$BUCKET" --s3-prefix "$STACK_NAME" \
  --output-template-file .packaged.yaml >/dev/null

# Parameters not passed keep their current value on updates.
PARAMS=()
if [ -n "${ALERT_EMAIL:-}" ]; then PARAMS+=("AlertEmail=${ALERT_EMAIL}"); fi
if [ -n "${SIMULATOR_ENABLED:-}" ]; then PARAMS+=("SimulatorEnabled=${SIMULATOR_ENABLED}"); fi
if [ -n "$DASHBOARD_URL" ]; then PARAMS+=("DashboardUrl=${DASHBOARD_URL}"); fi
DEPLOY_ARGS=(--template-file .packaged.yaml --stack-name "$STACK_NAME"
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset)
if [ ${#PARAMS[@]} -gt 0 ]; then DEPLOY_ARGS+=(--parameter-overrides "${PARAMS[@]}"); fi

echo "==> Deploying CloudFormation stack (2-4 minutes the first time)"
aws cloudformation deploy "${DEPLOY_ARGS[@]}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API_URL="$(out ApiUrl)"
SIM_FN="$(out SimulatorFunctionName)"
DEVICE_KEY="$(out DeviceApiKey)"

if [ "$FIRST_DEPLOY" = true ] || [ "${BACKFILL:-0}" = 1 ]; then
  echo "==> Seeding 14 days of history and the first forecast (about a minute)"
  aws lambda invoke --function-name "$SIM_FN" --cli-binary-format raw-in-base64-out --cli-read-timeout 320 \
    --payload '{"mode":"backfill","days":14}' /tmp/sce-backfill.json >/dev/null
  cat /tmp/sce-backfill.json; echo
fi

echo "==> Smoke test: GET ${API_URL}/summary"
curl -sf "${API_URL}/summary" | head -c 300 || echo "(API not reachable yet - retry in a minute)"
echo
IOT_ENDPOINT="$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text 2>/dev/null || echo 'n/a')"

cat <<EOF

================================================================================
 Smart Campus Energy Management System is live
--------------------------------------------------------------------------------
 REST API       : ${API_URL}
 Dashboard      : ${DASHBOARD_URL:-<your GitHub Pages URL>}?api=${API_URL}
 Device key     : ${DEVICE_KEY}   (header x-device-key, keep it private)
 IoT endpoint   : ${IOT_ENDPOINT}   topic campus/energy/<room_id>/readings

 Try it:
   curl ${API_URL}/summary
   # force an anomaly for a live demo (shows up on the dashboard within seconds)
   aws lambda invoke --function-name ${SIM_FN} --cli-binary-format raw-in-base64-out \\
     --payload '{"mode":"inject","scenario":"waste","room_id":"AB1-101"}' /tmp/out.json
   # stream the local Python simulator into the cloud
   python3 simulator/simulate.py --api ${API_URL} --key ${DEVICE_KEY} --rooms IOT-LAB-1 --interval 30
================================================================================
EOF
