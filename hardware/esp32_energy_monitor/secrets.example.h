// Copy this file to secrets.h (git-ignored) and fill in your values.
#pragma once

#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// Which room this meter is installed in. Use a campus room id (e.g. AB1-101)
// or a new id such as IOT-LAB-1 - unknown rooms are auto-registered.
#define ROOM_ID "IOT-LAB-1"

// ---- HTTPS mode (USE_MQTT 0): values printed by deploy.sh
#define API_URL "https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com"
#define DEVICE_KEY "paste-DeviceApiKey-here"

// ---- MQTT mode (USE_MQTT 1): values from iot/create-iot-device.sh
#define THING_NAME "campus-meter-IOT-LAB-1"
#define AWS_IOT_ENDPOINT "xxxxxxxxxxxxxx-ats.iot.ap-south-1.amazonaws.com"

// Amazon Root CA 1 (public) - https://www.amazontrust.com/repository/AmazonRootCA1.pem
static const char AMAZON_ROOT_CA1[] = R"EOF(
-----BEGIN CERTIFICATE-----
paste AmazonRootCA1.pem here
-----END CERTIFICATE-----
)EOF";

// MQTT mode only: device certificate + private key from iot/certs/<thing>/
static const char DEVICE_CERT[] = R"KEY(
-----BEGIN CERTIFICATE-----
paste device.pem.crt here
-----END CERTIFICATE-----
)KEY";

static const char DEVICE_PRIVATE_KEY[] = R"KEY(
-----BEGIN RSA PRIVATE KEY-----
paste private.pem.key here
-----END RSA PRIVATE KEY-----
)KEY";
