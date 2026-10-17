#!/bin/sh
# Compiles fusion.cpp + these tests with the host g++ (no Arduino toolchain) and runs them.
set -e
here=$(cd "$(dirname "$0")" && pwd)
fw="$here/../../src/esp32_argus"
out=$(mktemp -d)
g++ -std=c++17 -Wall -Wextra -I"$here/shim" -I"$fw" "$here/fusion_host_test.cpp" "$fw/fusion.cpp" -o "$out/fusion_host_test"
"$out/fusion_host_test"
