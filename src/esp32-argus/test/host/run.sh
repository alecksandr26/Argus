#!/bin/sh
# Compiles each *_host_test.cpp with the host g++ (no Arduino toolchain) against shim/Arduino.h
# and the firmware sources it names, then runs it.
#
# A test file lists the firmware sources it links in a "// SOURCES: a.cpp b.cpp" line, and can
# declare "// NEEDS: arduinojson" (the ArduinoJson headers, from $ARDUINOJSON_DIR or the Arduino
# IDE's library folder). Tests named in KNOWN_FAILING are expected to fail (to compile or to
# pass) until their bug is fixed: they report XFAIL; one that passes reports XPASS and fails the
# run until its KNOWN_FAILING line is removed.
here=$(cd "$(dirname "$0")" && pwd)
fw="$here/../../src/esp32_argus"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

find_arduinojson() {
  for d in "$ARDUINOJSON_DIR" \
    "$HOME/Arduino/libraries/ArduinoJson/src" \
    /mnt/c/Users/*/OneDrive/Documents/Arduino/libraries/ArduinoJson/src \
    /mnt/c/Users/*/Documents/Arduino/libraries/ArduinoJson/src; do
    [ -n "$d" ] && [ -f "$d/ArduinoJson.h" ] && { echo "$d"; return; }
  done
}
ajson=$(find_arduinojson)

known() { grep -q "^$1[[:space:]]" "$here/KNOWN_FAILING"; }

pass=0; xfail=0; bad=0; skip=0
for test in "$here"/*_host_test.cpp; do
  name=$(basename "$test" .cpp)
  sources=$(sed -n 's|^// SOURCES:||p' "$test")
  includes="-I$here/shim -I$here -I$fw"
  if grep -q "^// NEEDS: arduinojson" "$test"; then
    if [ -z "$ajson" ]; then
      echo "SKIP  $name (ArduinoJson not found; set ARDUINOJSON_DIR)"; skip=$((skip + 1)); continue
    fi
    includes="$includes -I$ajson"
  fi
  srcs=""
  for s in $sources; do srcs="$srcs $fw/$s"; done

  ok=0
  if g++ -std=c++17 -Wall -Wextra $includes "$test" $srcs -o "$out/$name" 2>"$out/$name.log" \
    && "$out/$name" >>"$out/$name.log" 2>&1; then
    ok=1
  fi

  if known "$name"; then
    if [ $ok = 1 ]; then
      echo "XPASS $name -- fixed? remove its line from KNOWN_FAILING"; bad=$((bad + 1))
    else
      echo "XFAIL $name"; xfail=$((xfail + 1))
    fi
  elif [ $ok = 1 ]; then
    echo "PASS  $name"; pass=$((pass + 1))
  else
    echo "FAIL  $name"; sed 's/^/      /' "$out/$name.log" | head -40; bad=$((bad + 1))
  fi
done

echo "$pass passed, $xfail xfailed, $skip skipped, $bad failed"
[ $bad = 0 ]
