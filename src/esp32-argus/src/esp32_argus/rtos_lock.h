#pragma once

// A scoped FreeRTOS mutex lock. The firmware runs three tasks (the Arduino loop: Bluetooth
// sessions with the Pi; gpsTask: the NEO-6M; senderTask: the backend), and what they share is
// guarded by one mutex each (the sketch's shared state, the credentials). Hold a lock only for
// short, non-blocking work: never across an HTTP attempt, a Bluetooth exchange or an NVS write
// that can wait.

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

class RtosLock {
 public:
  explicit RtosLock(SemaphoreHandle_t mutex) : mutex_(mutex) {
    if (mutex_) xSemaphoreTake(mutex_, portMAX_DELAY);
  }
  ~RtosLock() {
    if (mutex_) xSemaphoreGive(mutex_);
  }
  RtosLock(const RtosLock&) = delete;
  RtosLock& operator=(const RtosLock&) = delete;

 private:
  SemaphoreHandle_t mutex_;
};
