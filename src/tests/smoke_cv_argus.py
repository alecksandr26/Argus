#!/usr/bin/env python3
"""Manual smoke-check script for cv-argus's sender/ module.

NOT part of cv-argus's own hermetic pytest suite (src/cv-argus/tests/) -- same real
SenderServer/Buffer/Alert/protocol code those tests already exercise, just run here as a
readable, logged script instead of pytest asserts, so you can `python` it and watch it work.

"Simulates the server": this project's real Bluetooth SPP SERVER is cv-argus's own
sender.server.SenderServer -- the piece src/esp32-argus's firmware connects to as a *client*
and polls with PULL/ACK (src/cv-argus/src/sender/protocol.py). Real Bluetooth
hardware/passthrough isn't available in any session that's touched this repo (see
src/cv-argus/CLAUDE.md's "Open decisions" -- neither compose file wires it up yet), so this
script runs the real SenderServer against cv-argus's own in-memory FakeTransport/FakeListener
pair -- the exact stand-in cv-argus's own unit tests use -- and plays the client's part by hand
against it, printing every step. This proves the real server/buffer/alerts/protocol code works
end to end; it does NOT prove real Bluetooth connectivity, which needs real hardware no
session has had access to.

No install needed, no Docker needed. `alerts/`, `buffer/`, and `sender/` are deliberately
dependency-light -- pure stdlib (dataclasses/enum/json/sqlite3/socket/threading, no numpy/
mediapipe/tensorflow/opencv -- verified directly against every file in that import chain,
including constants.py). So rather than `pip install -e src/cv-argus` (which would pull in that
full heavy dependency list for nothing, since this script never touches model/ or pipeline/),
this script registers `cv_argus` as a module pointing straight at src/cv-argus/src/ itself,
below -- the same `package_dir={"cv_argus": "src"}` mapping setup.py does for a real install,
just done at runtime via importlib instead of setuptools. A plain `sys.path.insert` alone
wouldn't work here: that directory is named `src` on disk, not `cv_argus`, so Python wouldn't
find anything under the name this code's relative imports (`from ..alerts import ...` etc.)
expect.

Usage:
    python src/tests/smoke_cv_argus.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

_CV_ARGUS_SRC = Path(__file__).resolve().parent.parent / "cv-argus" / "src"

if "cv_argus" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "cv_argus",
        _CV_ARGUS_SRC / "__init__.py",
        submodule_search_locations=[str(_CV_ARGUS_SRC)],
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["cv_argus"] = _module
    _spec.loader.exec_module(_module)

try:
    from cv_argus.alerts import Alert, AlertKind
    from cv_argus.buffer import open_buffer
    from cv_argus.sender import (
        END_LINE,
        FakeListener,
        FakeTransport,
        SenderServer,
        format_ack,
        format_acked,
        format_pull,
    )
except ImportError as exc:
    print(f"[FAIL] cv_argus isn't importable even from {_CV_ARGUS_SRC}: {exc}")
    print()
    print(f"Check that this path actually exists and looks right: {_CV_ARGUS_SRC}")
    print("(this script expects src/tests/ and src/cv-argus/src/ to be siblings under src/ --")
    print(" if the repo got reorganized, update _CV_ARGUS_SRC above to match)")
    sys.exit(1)

_FAILURES: list[str] = []


def _log(ok: bool, label: str, detail: str = "") -> None:
    tag = "[ OK ]" if ok else "[FAIL]"
    print(f"{tag} {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        _FAILURES.append(label)


def main() -> int:
    print(
        "cv-argus sender/ smoke check (real server code, simulated transport -- not real "
        "Bluetooth)\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        buffer = open_buffer(Path(tmp) / "smoke_buffer.sqlite3")

        # Seed one real Alert -- the exact envelope shape src/esp32-argus/README.md section 1
        # documents the ESP32 pulling.
        alert = Alert.new(
            kind=AlertKind.DROWSINESS,
            level=2,
            source_id="smoke-cam-0",
            payload={"class_name": "Drowsy", "probabilities": [0.12, 0.88]},
        )
        buffer.enqueue(alert)
        _log(True, "seeded one Alert into a real Buffer", f"id={alert.id}")

        client_transport, server_transport = FakeTransport.pair()
        listener = FakeListener()
        listener.offer(server_transport)  # "a client connects" -- queued for the accept loop

        server = SenderServer(buffer, listener)
        server.start()
        _log(True, "started a real SenderServer (accept loop running on its own thread)")

        try:
            client_transport.write((format_pull(10) + "\n").encode())
            _log(True, "client -> server: PULL 10")

            received: list[str] = []
            while True:
                line = client_transport.readline(timeout=3.0)
                if line is None:
                    _log(False, "client <- server", "timed out waiting for a line")
                    break
                text = line.decode()
                if text == END_LINE:
                    _log(True, "client <- server: END")
                    break
                received.append(text)
                print(f"       client <- server: {text}")

            _log(
                len(received) == 1,
                "received exactly the one seeded Alert back",
                f"got {len(received)} record(s)",
            )

            if received:
                client_transport.write((format_ack([alert.id]) + "\n").encode())
                _log(True, f"client -> server: {format_ack([alert.id])}")

                acked_line = client_transport.readline(timeout=3.0)
                acked_text = acked_line.decode() if acked_line is not None else None
                _log(
                    acked_text == format_acked(1),
                    "client <- server: ACKED",
                    f"got {acked_text!r}",
                )
        finally:
            server.stop()
            server.join(timeout=2.0)
            _log(not server.is_alive, "server stopped cleanly")

        _log(buffer.unsent_count() == 0, "buffer shows 0 unsent after the ACK round trip")
        buffer.close()

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print(
        "All checks passed -- the real sender/buffer/alerts/protocol code works end to end "
        "(simulated transport, not real Bluetooth hardware)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
