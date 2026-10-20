/*
 * Smart Campus Energy Monitor - ESP32 node (optional real hardware)
 *
 *   ESP32 DevKit + PZEM-004T v3.0 (voltage/current/power/energy/PF)
 *   + HC-SR501 PIR (occupancy) + optional DHT22 (temperature)
 *
 * Sends one reading per minute to the cloud, either
 *   USE_MQTT 0 : HTTPS POST to API Gateway  /readings   (header x-device-key)
 *   USE_MQTT 1 : MQTT/TLS to AWS IoT Core   campus/energy/<ROOM_ID>/readings
 *
 * Libraries (Arduino Library Manager): "PZEM004Tv30" by Jakub Mandula,
 *   "PubSubClient" by Nick O'Leary (MQTT only), "DHT sensor library" by Adafruit (DHT only).
 *
 * Copy secrets.example.h to secrets.h and fill in your values first.
 * SAFETY: the PZEM-004T is connected to 230 V mains. Get the wiring checked
 * by your lab in-charge / electrician and never work on a live circuit.
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <PZEM004Tv30.h>
#include <time.h>
#include "secrets.h"

#define USE_MQTT 0
#define USE_DHT 0

#if USE_MQTT
#include <PubSubClient.h>
#endif
#if USE_DHT
#include <DHT.h>
DHT dht(4, DHT22);  // DHT22 data pin -> GPIO 4
#endif

const int PIR_PIN = 27;                                  // HC-SR501 OUT -> GPIO 27
const uint32_t READ_INTERVAL_MS = 60UL * 1000UL;         // one reading per minute
const uint32_t OCCUPANCY_WINDOW_MS = 5UL * 60UL * 1000UL;  // "occupied" if motion in last 5 min

PZEM004Tv30 pzem(Serial2, 16, 17);  // PZEM TX -> GPIO16 (RX2), PZEM RX -> GPIO17 (TX2)
WiFiClientSecure net;
#if USE_MQTT
PubSubClient mqtt(net);
#endif

volatile uint32_t lastMotionMs = 0;
void IRAM_ATTR onMotion() { lastMotionMs = millis(); }

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to Wi-Fi %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print('.');
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " FAILED (will retry)");
}

void syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  for (int i = 0; i < 20 && time(nullptr) < 1700000000; i++) delay(500);
}

#if USE_MQTT
void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("Connecting to AWS IoT Core... ");
    if (mqtt.connect(THING_NAME)) {
      Serial.println("ok");
    } else {
      Serial.printf("failed rc=%d, retrying in 5 s\n", mqtt.state());
      delay(5000);
    }
  }
}
#endif

bool sendReading(const char *json) {
#if USE_MQTT
  char topic[96];
  snprintf(topic, sizeof topic, "campus/energy/%s/readings", ROOM_ID);
  connectMqtt();
  return mqtt.publish(topic, json);
#else
  HTTPClient http;
  String url = String(API_URL) + "/readings";
  http.begin(net, url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("x-device-key", DEVICE_KEY);
  int code = http.POST((uint8_t *)json, strlen(json));
  Serial.printf("POST %d %s\n", code, http.getString().c_str());
  http.end();
  return code == 200;
#endif
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIR_PIN), onMotion, RISING);
#if USE_DHT
  dht.begin();
#endif
  connectWiFi();
  syncClock();
  net.setCACert(AMAZON_ROOT_CA1);  // validates both API Gateway and IoT Core certificates
#if USE_MQTT
  net.setCertificate(DEVICE_CERT);
  net.setPrivateKey(DEVICE_PRIVATE_KEY);
  mqtt.setServer(AWS_IOT_ENDPOINT, 8883);
  mqtt.setBufferSize(512);
#endif
}

void loop() {
  static uint32_t lastSend = 0;
#if USE_MQTT
  if (mqtt.connected()) mqtt.loop();
#endif
  if (lastSend && millis() - lastSend < READ_INTERVAL_MS) {
    delay(50);
    return;
  }
  lastSend = millis();
  connectWiFi();

  float voltage = pzem.voltage();
  float current = pzem.current();
  float power = pzem.power();
  float energy = pzem.energy();
  float pf = pzem.pf();
  if (isnan(power) || isnan(voltage)) {
    Serial.println("PZEM read failed - check wiring / 230 V supply to the PZEM");
    return;
  }
  int occupied = (lastMotionMs != 0 && millis() - lastMotionMs < OCCUPANCY_WINDOW_MS) ? 1 : 0;

  char extra[48] = "";
#if USE_DHT
  float t = dht.readTemperature();
  if (!isnan(t)) snprintf(extra, sizeof extra, ",\"temperature_c\":%.1f", t);
#endif

  char json[400];
  snprintf(json, sizeof json,
           "{\"room_id\":\"%s\",\"ts\":%ld,\"power_w\":%.1f,\"voltage_v\":%.1f,\"current_a\":%.3f,"
           "\"power_factor\":%.2f,\"occupancy\":%d,\"meter_kwh_total\":%.3f%s,\"source\":\"esp32\"}",
           ROOM_ID, (long)time(nullptr), power, voltage, current, pf, occupied, energy, extra);
  Serial.println(json);
  if (!sendReading(json)) Serial.println("send failed - will retry next cycle");
}
