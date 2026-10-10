#include "grip_sensor.h"

#include "config.h"

void gripSensorBegin() {
  pinMode(GRIP_SENSOR_PIN, INPUT_PULLUP);
}

GripState readGripState() {
  // Active-low placeholder wiring: LOW = hand detected on the wheel = good grip. Swap
  // this for whatever the real sensor's actual signal convention turns out to be once
  // one is chosen -- see config.h's caveat.
  return digitalRead(GRIP_SENSOR_PIN) == LOW ? GripState::GOOD : GripState::BAD;
}
