#include "panic_button.h"

#include "config.h"

namespace {
int lastRawState = HIGH;  // active-low with INPUT_PULLUP: HIGH = not pressed
unsigned long lastChangeAt = 0;
bool armed = true;  // false while held down, so one press fires exactly once
}  // namespace

void panicButtonBegin() {
  pinMode(PANIC_BUTTON_PIN, INPUT_PULLUP);
}

bool panicButtonPressed() {
  int raw = digitalRead(PANIC_BUTTON_PIN);
  unsigned long now = millis();

  if (raw != lastRawState) {
    lastChangeAt = now;
    lastRawState = raw;
  }

  if (now - lastChangeAt < PANIC_BUTTON_DEBOUNCE_MS) {
    return false;  // still settling
  }

  if (raw == LOW && armed) {
    armed = false;
    return true;
  }
  if (raw == HIGH) {
    armed = true;
  }
  return false;
}
