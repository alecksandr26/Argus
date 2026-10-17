// Minimal Arduino.h stand-in so fusion.cpp (pure logic, no hardware calls) compiles and runs
// on a dev machine. Only what fusion.* actually uses.
#pragma once
#include <cstdint>
#include <string>

using String = std::string;
