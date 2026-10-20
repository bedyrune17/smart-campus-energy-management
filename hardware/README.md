# Optional hardware node: ESP32 + PZEM-004T

The system runs fully on simulated data. The firmware here lets you replace one
simulated room with a real meter.

## Bill of materials (about ₹1,500)

| Part | Purpose |
|---|---|
| ESP32 DevKit V1 | Wi-Fi microcontroller |
| PZEM-004T v3.0 + 100 A split-core CT | voltage, current, power, energy, power factor |
| HC-SR501 PIR sensor | room occupancy (motion in the last 5 min) |
| DHT22 (optional) | room temperature |
| 5 V supply, jumper wires, enclosure | |

## Wiring

| From | To |
|---|---|
| PZEM 5V / GND | ESP32 VIN (5V) / GND |
| PZEM TX | ESP32 GPIO16 (RX2) |
| PZEM RX | ESP32 GPIO17 (TX2) |
| PZEM L / N terminals | 230 V supply of the circuit being measured |
| CT clamp | around the **live** wire of that circuit only |
| PIR OUT / VCC / GND | GPIO27 / 5V / GND |
| DHT22 DATA / VCC / GND | GPIO4 / 3.3V / GND (10 kΩ pull-up) |

> **Mains safety:** the PZEM-004T's L/N side is at 230 V. Have the lab in-charge
> or an electrician do the mains connection, keep it in a closed enclosure, and
> never touch it while powered. The CT clamp itself is non-invasive.

## Flashing

1. Arduino IDE, add the ESP32 board package, then install the libraries **PZEM004Tv30**
   (Jakub Mandula), and if needed **PubSubClient** and **DHT sensor library**.
2. Copy `secrets.example.h` to `secrets.h` and fill in Wi-Fi, `ROOM_ID` and either
   - **HTTPS mode** (`USE_MQTT 0`, simplest): `API_URL` and `DEVICE_KEY` from the `deploy.sh` output, or
   - **MQTT mode** (`USE_MQTT 1`, AWS IoT Core): run `bash iot/create-iot-device.sh <ROOM_ID>` in
     CloudShell and paste the endpoint, certificate and private key it creates.
3. Paste Amazon Root CA 1 into `AMAZON_ROOT_CA1`, then flash and open the serial monitor (115200 baud).

The node sends one reading per minute. Use a new id such as `IOT-LAB-1` and the
room appears on the dashboard automatically. If you reuse a simulated room id,
deploy with `SIMULATOR_ENABLED=false` so the virtual and real meters don't both report.
