#pragma once

// The cabin buzzer on BUZZER_PIN (docs/designs/grip-alarm-contracts.md C4): an active buzzer, a
// continuous tone while the pin is on. Only the GPIO write lives here; when it sounds is
// alarm_policy.h's decision. Only the sketch's gripTask calls this (it samples the wheel and
// applies the alarm every GRIP_SAMPLE_INTERVAL_MS).

void buzzerBegin();          // the pin as an output, off
void buzzerSet(bool on);     // writes the pin only when the state changes
bool buzzerIsOn();
