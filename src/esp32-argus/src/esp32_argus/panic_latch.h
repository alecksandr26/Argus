#pragma once

// The panic button must never be missed or lost (PAN-01/PAN-03). Pure logic, no Arduino calls,
// so test/host/panic_latch_host_test.cpp runs it on the PC.
//
// - PanicLatch: the button's interrupt calls onFallingEdge(); the main loop takes the press
//   whenever it gets to it, even after a multi-second Bluetooth or HTTP call. Debounced: edges
//   within `debounceMs` of the last accepted one are contact bounce.
// - PanicQueue: pressed panics waiting for the backend, kept in NVS (serialize()) so a reboot
//   doesn't lose them, and retried until the backend confirms one. Bounded: the oldest press is
//   dropped first (the SMS already went out for each).

#include <cstdint>
#include <string>

class PanicLatch {
 public:
  explicit PanicLatch(uint32_t debounceMs) : debounceMs_(debounceMs) {}

  // From the interrupt: nowMs is millis(). Safe against millis() wrapping.
  void onFallingEdge(uint32_t nowMs) {
    if (accepted_ && (uint32_t)(nowMs - lastAcceptedMs_) < debounceMs_) return;
    accepted_ = true;
    lastAcceptedMs_ = nowMs;
    pending_ = true;
  }

  // From the main loop: true once per accepted press.
  bool takePending() {
    if (!pending_) return false;
    pending_ = false;
    return true;
  }

 private:
  uint32_t debounceMs_;
  volatile bool pending_ = false;
  volatile bool accepted_ = false;
  volatile uint32_t lastAcceptedMs_ = 0;
};

struct PanicEvent {
  uint32_t atMs = 0;      // millis() at the press (this boot's clock; for logs and ordering)
  bool hasFix = false;    // the position at the press, if there was a fix
  double lat = 0.0;
  double lon = 0.0;
  std::string occurredAt;     // GPS UTC time at the press, or "" (then server time)
  std::string clientEventId;  // so a retry after a lost answer can't duplicate it
};

class PanicQueue {
 public:
  static const int kCapacity = 8;

  void push(const PanicEvent& event);
  int size() const { return count_; }
  const PanicEvent& front() const { return events_[head_]; }
  void markFrontPosted();

  // A compact text form for NVS; deserialize() returns false (and leaves the queue empty) on
  // anything that isn't one.
  std::string serialize() const;
  bool deserialize(const std::string& blob);

 private:
  PanicEvent events_[kCapacity];
  int head_ = 0;
  int count_ = 0;
};
