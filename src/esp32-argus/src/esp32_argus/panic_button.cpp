#include "panic_button.h"

#include "config.h"
#include "panic_latch.h"

namespace {
// Fed by the falling edge itself, so a press while loop() is stuck in a multi-second Bluetooth
// or HTTP call is still caught (PAN-01); loop() takes it whenever it gets there.
PanicLatch latch(PANIC_BUTTON_DEBOUNCE_MS);

void IRAM_ATTR onPanicEdge() { latch.onFallingEdge(millis()); }
}  // namespace

void panicButtonBegin() {
  pinMode(PANIC_BUTTON_PIN, INPUT_PULLUP);  // active-low: pressed pulls the pin to GND
  attachInterrupt(digitalPinToInterrupt(PANIC_BUTTON_PIN), onPanicEdge, FALLING);
}

bool panicButtonPressed() { return latch.takePending(); }
