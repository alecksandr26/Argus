#!/bin/sh
# Compiles the firmware for the real target (classic ESP32, core 3.x, Huge APP) with every
# warning on, without opening the IDE. Uses `arduino-cli` from PATH, or $ARDUINO_CLI. From WSL it
# can be the Windows binary the Arduino IDE bundles, e.g.
#
#   ARDUINO_CLI="/mnt/c/Users/<you>/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe" \
#     sh src/esp32-argus/test/compile.sh
#
# (a Windows arduino-cli needs Windows paths: they are converted with wslpath). Needs the esp32
# core and the ArduinoJson + TinyGPSPlus libraries installed for that arduino-cli (see
# src/esp32_argus/CLAUDE.md). Prints our own warnings/errors and the flash/RAM use; exits non-zero
# when the build fails. Flashing stays a manual step (Arduino IDE, the board's COM port).
here=$(cd "$(dirname "$0")" && pwd)
sketch="$here/../src/esp32_argus"
cli=${ARDUINO_CLI:-arduino-cli}
build=${ESP32_BUILD_DIR:-}
log=$(mktemp)
trap 'rm -f "$log"' EXIT

case "$cli" in
  *.exe)
    sketch_arg=$(wslpath -w "$sketch")
    [ -n "$build" ] && build=$(wslpath -w "$build")
    cd /tmp || exit 1  # a Windows binary can't start in a \\wsl$ working directory
    ;;
  *) sketch_arg=$sketch ;;
esac

set -- compile --fqbn esp32:esp32:esp32:PartitionScheme=huge_app --warnings all
[ -n "$build" ] && set -- "$@" --build-path "$build"
"$cli" "$@" "$sketch_arg" >"$log" 2>&1
status=$?

grep -E 'esp32_argus[\\/][A-Za-z0-9_]+\.(cpp|h|ino):[0-9]+:[0-9]+: (warning|error)' "$log" \
  | sed -E 's|^.*esp32_argus[\\/]||' | sort -u
grep -E 'Sketch uses|Global variables' "$log"
if [ $status -ne 0 ]; then
  echo "BUILD FAILED (arduino-cli exit $status); full log:"
  cat "$log"
fi
exit $status
