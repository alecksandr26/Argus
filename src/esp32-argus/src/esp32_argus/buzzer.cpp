#include "buzzer.h"

#include <Arduino.h>

#include "config.h"

namespace {
bool buzzing = false;

void writePin(bool sound) {
  bool high = BUZZER_ACTIVE_HIGH ? sound : !sound;
  digitalWrite(BUZZER_PIN, high ? HIGH : LOW);
}
}  // namespace

void buzzerBegin() {
  pinMode(BUZZER_PIN, OUTPUT);
  writePin(false);
  buzzing = false;
}

void buzzerSet(bool sound) {
  if (sound == buzzing) return;
  writePin(sound);
  buzzing = sound;
}

bool buzzerIsOn() { return buzzing; }
