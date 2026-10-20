#!/usr/bin/env bash
# Register a physical ESP32 as an AWS IoT Core "thing" with its own X.509 certificate.
# Run in AWS CloudShell:  bash iot/create-iot-device.sh AB1-101
# Then copy the files in iot/certs/<thing>/ into hardware/esp32_energy_monitor/secrets.h (see that file).
set -euo pipefail

ROOM_ID="${1:?usage: create-iot-device.sh <room_id>   e.g. AB1-101 or IOT-LAB-1}"
THING="campus-meter-${ROOM_ID}"
POLICY="smart-campus-device-policy"
DIR="$(dirname "$0")/certs/${THING}"
export AWS_PAGER=""
mkdir -p "$DIR"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGION="$(aws configure get region 2>/dev/null || echo "${AWS_REGION:-ap-south-1}")"

aws iot create-thing --thing-name "$THING" >/dev/null 2>&1 || echo "thing ${THING} already exists"

# Least privilege: a device may only connect as itself and publish its own room's readings.
cat > "${DIR}/policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "iot:Connect",
      "Resource": "arn:aws:iot:${REGION}:${ACCOUNT_ID}:client/\${iot:Connection.Thing.ThingName}" },
    { "Effect": "Allow", "Action": "iot:Publish",
      "Resource": "arn:aws:iot:${REGION}:${ACCOUNT_ID}:topic/campus/energy/*/readings" }
  ]
}
EOF
aws iot create-policy --policy-name "$POLICY" --policy-document "file://${DIR}/policy.json" >/dev/null 2>&1 \
  || echo "policy ${POLICY} already exists"

CERT_ARN="$(aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile "${DIR}/device.pem.crt" \
  --private-key-outfile "${DIR}/private.pem.key" \
  --public-key-outfile "${DIR}/public.pem.key" \
  --query certificateArn --output text)"
aws iot attach-policy --policy-name "$POLICY" --target "$CERT_ARN"
aws iot attach-thing-principal --thing-name "$THING" --principal "$CERT_ARN"
curl -sf https://www.amazontrust.com/repository/AmazonRootCA1.pem -o "${DIR}/AmazonRootCA1.pem"

ENDPOINT="$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)"
cat <<EOF

Device registered.
  Thing name (MQTT client id): ${THING}
  Endpoint                   : ${ENDPOINT}  (port 8883)
  Publish topic              : campus/energy/${ROOM_ID}/readings
  Certificates               : ${DIR}/   (never commit these - .gitignore already excludes them)

Test without hardware:
  aws iot-data publish --endpoint-url https://${ENDPOINT} --topic campus/energy/${ROOM_ID}/readings --cli-binary-format raw-in-base64-out \\
    --payload '{"room_id":"${ROOM_ID}","power_w":4200,"voltage_v":231,"occupancy":0}'
EOF
