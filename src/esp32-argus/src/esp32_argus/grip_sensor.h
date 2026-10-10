#pragma once

#include "fusion.h"

// Wired directly to this device per the root CLAUDE.md -- never feeds the Pi's
// AI-orchestrator decision node. Feeds this device's own fusion decision loop instead
// (see fusion.h), combined with the Pi's relayed drowsiness classification.

void gripSensorBegin();

// GPIO mapping not finalized against real hardware yet -- see config.h /
// src/esp32-argus/README.md section 4.
GripState readGripState();
