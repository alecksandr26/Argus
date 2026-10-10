#pragma once

#include <Arduino.h>

// Wired directly to this device per the root CLAUDE.md -- never routed through cv-argus's
// orchestrator, bypasses the drowsy+grip fusion loop entirely (see fusion.h /
// src/esp32-argus/README.md section 5: "Panic button bypasses all of the above").

void panicButtonBegin();

// Debounced and edge-triggered -- returns true at most once per physical press, not
// continuously while held down.
bool panicButtonPressed();
