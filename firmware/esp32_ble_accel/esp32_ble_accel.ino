// ESP32 BLE accelerometer peripheral for the Motion Hub demo.
//
// Advertises service FFE0 and streams x/y/z as three little-endian float32
// (12 bytes) via notifications on characteristic FFE1 at ~20 Hz. The Python
// BLE bridge (backend/app/ble_bridge.py) picks it up automatically.
//
// Wire an MPU6050 to SDA=21 / SCL=22 and uncomment USE_MPU6050 for real data;
// otherwise it streams a synthetic signal so the pipeline can be demoed
// with nothing but a bare ESP32 dev board.
//
// Arduino IDE: board "ESP32 Dev Module"; BLE library ships with the ESP32 core.

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// #define USE_MPU6050
#ifdef USE_MPU6050
#include <Wire.h>
#define MPU_ADDR 0x68
#endif

#define SERVICE_UUID "0000ffe0-0000-1000-8000-00805f9b34fb"
#define CHAR_UUID    "0000ffe1-0000-1000-8000-00805f9b34fb"
#define DEVICE_NAME  "ESP32 Accel"

BLECharacteristic* characteristic;
bool connected = false;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { connected = true; }
  void onDisconnect(BLEServer* server) override {
    connected = false;
    server->startAdvertising();  // let the bridge reconnect
  }
};

void readAccel(float& x, float& y, float& z) {
#ifdef USE_MPU6050
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  // ACCEL_XOUT_H
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6, true);
  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();
  const float scale = 9.81f / 16384.0f;  // +/-2g range
  x = ax * scale; y = ay * scale; z = az * scale;
#else
  float t = millis() / 1000.0f;
  x = 2.0f * sinf(t * 2.0f);
  y = 1.5f * cosf(t * 1.3f);
  z = 9.81f + 0.5f * sinf(t * 5.0f);
#endif
}

void setup() {
  Serial.begin(115200);
#ifdef USE_MPU6050
  Wire.begin(21, 22);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); Wire.write(0);  // wake up
  Wire.endTransmission(true);
#endif

  BLEDevice::init(DEVICE_NAME);
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService* service = server->createService(SERVICE_UUID);
  characteristic = service->createCharacteristic(
      CHAR_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  characteristic->addDescriptor(new BLE2902());  // enables notification subscriptions
  service->start();

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);  // the bridge filters scans on this UUID
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("advertising as " DEVICE_NAME);
}

void loop() {
  if (connected) {
    float v[3];
    readAccel(v[0], v[1], v[2]);
    characteristic->setValue((uint8_t*)v, sizeof(v));  // 3 x float32 LE
    characteristic->notify();
  }
  delay(50);  // ~20 Hz
}
