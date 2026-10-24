#pragma once

// The cabin buzzer (docs/designs/grip-alarm-contracts.md C2/C4, minimal version: no SMS, no
// no_grip event to the backend yet). It sounds while either alarm is on:
//
// - Grip: the 4 RFP602 pressure sensors on the wheel (GRIP_SENSOR_PIN_1..4). No sensor above
//   GRIP_PRESSURE_THRESHOLD for GRIP_ALERT_MS -> on; any pressure back -> off at once.
// - Vision: a live critical from the Pi whose reason isn't "no_face" -> on; the Pi's driver OK ->
//   off (or VISION_ALARM_MAX_MS, in case the Pi never sends one). MEDIUM never sounds.
//
// cabinAlarmTask() samples the sensors and drives the buzzer on its own task, so a Bluetooth
// session or an HTTP attempt never delays it.

#include <string>

#include "pi_event_policy.h"

void cabinAlarmBegin();  // pins; call in setup() before starting cabinAlarmTask
void cabinAlarmTask(void*);

// From the loop task, for every record of a live (not DLQ) session.
void cabinAlarmOnPiRecord(PiEventKind kind, const std::string& reason, const std::string& episode);
