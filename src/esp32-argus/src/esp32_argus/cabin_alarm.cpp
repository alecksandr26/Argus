#include "cabin_alarm.h"

#include <Arduino.h>

#include "config.h"
#include "rtos_lock.h"

namespace {

const int kGripPins[4] = {GRIP_SENSOR_PIN_1, GRIP_SENSOR_PIN_2, GRIP_SENSOR_PIN_3,
                          GRIP_SENSOR_PIN_4};

SemaphoreHandle_t visionMutex = nullptr;
bool visionOn = false;            // guarded by visionMutex
uint32_t visionSinceMs = 0;
std::string visionEpisode;        // the episode that turned it on
std::string lastOkEpisode;        // so a re-pulled critical of an ended episode can't restart it

bool gripAlarm = false;           // cabinAlarmTask only
bool gripCounting = false;
uint32_t noPressureSinceMs = 0;
uint32_t lastGripLogMs = 0;
bool buzzerOn = false;

void setBuzzer(bool on) {
  if (on == buzzerOn) return;
  buzzerOn = on;
  digitalWrite(BUZZER_PIN, (on == (BUZZER_ACTIVE_HIGH != 0)) ? HIGH : LOW);
  Serial.printf("[alarm] buzzer %s\n", on ? "ON" : "off");
}

bool readPressure(uint32_t now) {
  int v[4];
  bool pressure = false;
  for (int i = 0; i < 4; ++i) {
    v[i] = analogRead(kGripPins[i]);
    if (v[i] > GRIP_PRESSURE_THRESHOLD) pressure = true;
  }
  if (now - lastGripLogMs >= GRIP_LOG_INTERVAL_MS) {
    lastGripLogMs = now;
    Serial.printf("[grip] S1=%d S2=%d S3=%d S4=%d -> %s\n", v[0], v[1], v[2], v[3],
                  pressure ? "pressure" : "NO pressure");
  }
  return pressure;
}

void updateGrip(uint32_t now) {
  if (readPressure(now)) {
    gripCounting = false;
    if (gripAlarm) {
      gripAlarm = false;
      Serial.println("[grip] pressure back on the wheel -- grip alarm off");
    }
    return;
  }
  if (!gripCounting) {
    gripCounting = true;
    noPressureSinceMs = now;
  }
  if (!gripAlarm && now - noPressureSinceMs >= GRIP_ALERT_MS) {
    gripAlarm = true;
    Serial.println("[grip] ALERT: no pressure on the wheel -- grip alarm ON");
  }
}

bool visionAlarm(uint32_t now) {
  RtosLock lock(visionMutex);
  if (visionOn && now - visionSinceMs >= VISION_ALARM_MAX_MS) {
    visionOn = false;
    Serial.println("[alarm] vision alarm off: no driver OK from the Pi in time (safety cap)");
  }
  return visionOn;
}

}  // namespace

void cabinAlarmBegin() {
  visionMutex = xSemaphoreCreateMutex();
  analogReadResolution(12);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, BUZZER_ACTIVE_HIGH ? LOW : HIGH);
  Serial.printf("[alarm] grip sensors on GPIO %d/%d/%d/%d (threshold %d), buzzer on GPIO %d\n",
                kGripPins[0], kGripPins[1], kGripPins[2], kGripPins[3], GRIP_PRESSURE_THRESHOLD,
                BUZZER_PIN);
}

void cabinAlarmTask(void*) {
  for (;;) {
    uint32_t now = millis();
    updateGrip(now);
    setBuzzer(gripAlarm || visionAlarm(now));
    vTaskDelay(pdMS_TO_TICKS(GRIP_SAMPLE_INTERVAL_MS));
  }
}

void cabinAlarmOnPiRecord(PiEventKind kind, const std::string& reason, const std::string& episode) {
  if (kind != PiEventKind::AlertCritical && kind != PiEventKind::DriverOk) return;
  RtosLock lock(visionMutex);
  if (kind == PiEventKind::DriverOk) {
    if (!episode.empty()) lastOkEpisode = episode;
    if (visionOn) Serial.println("[alarm] driver OK from the Pi -- vision alarm off");
    visionOn = false;
    return;
  }
  if (reason == "no_face") return;  // the driver isn't visible: never sounds
  if (!episode.empty() && episode == lastOkEpisode) return;  // re-pulled after its OK
  if (visionOn && episode == visionEpisode) return;
  visionOn = true;
  visionSinceMs = millis();
  visionEpisode = episode;
  Serial.printf("[alarm] critical \"%s\" from the Pi -- vision alarm ON\n", reason.c_str());
}
