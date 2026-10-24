#pragma once

// The steering-wheel grip sensor (docs/designs/grip-alarm-contracts.md C2): four RFP602 pressure
// sensors on GRIP_SENSOR_PINS (ADC1: they keep working with Bluetooth/WiFi on), read as 12-bit
// analog values. Wired straight to this device per the root CLAUDE.md -- it never feeds the Pi.
// What the readings mean (pressure or not, the 3 s rule) is grip_policy.h's job; only the
// sketch's gripTask calls this.

#include "config.h"

void gripSensorBegin();  // analogReadResolution(12)

// The four raw readings (0..4095), in GRIP_SENSOR_PINS order, into `raw` (GRIP_SENSOR_COUNT).
void gripSensorRead(int* raw);
