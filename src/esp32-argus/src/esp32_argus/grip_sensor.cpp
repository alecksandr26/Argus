#include "grip_sensor.h"

#include <Arduino.h>

namespace {
const int kPins[GRIP_SENSOR_COUNT] = GRIP_SENSOR_PINS;
}  // namespace

void gripSensorBegin() {
  // As in the user's bench sketch: no pinMode, analogRead() sets the pin up itself. GPIO 34-39
  // are input-only and have no pull-ups, which an analog read doesn't need.
  analogReadResolution(12);
  Serial.print("[grip] Wheel sensors on GPIO");
  for (int pin : kPins) Serial.printf(" %d", pin);
  Serial.printf(", pressure = %d above each sensor's baseline (learned over the first %lu ms: "
                "keep the wheel untouched), alarm after %lu ms without it%s\n",
                GRIP_PRESSURE_DELTA, (unsigned long)GRIP_BASELINE_BOOT_MS,
                (unsigned long)GRIP_ALERT_MS,
                GRIP_REQUIRE_MOVING ? " while the truck moves (GRIP_REQUIRE_MOVING)" : "");
}

void gripSensorRead(int* raw) {
  for (int i = 0; i < GRIP_SENSOR_COUNT; i++) {
    long sum = 0;
    for (int k = 0; k < GRIP_ADC_SAMPLES; k++) sum += analogRead(kPins[i]);
    raw[i] = (int)(sum / GRIP_ADC_SAMPLES);
  }
}
