#!/usr/bin/env python3
"""Manual smoke-check script for cv-argus's sender/ module, Bluetooth protocol v2.

NOT part of cv-argus's own hermetic pytest suite (src/cv-argus/tests/) -- same real
SenderServer/Buffer/Alert/protocol code those tests already exercise, just run here as a
readable, logged script instead of pytest asserts, so you can `python` it and watch it work.

"Simulates the server": this project's real Bluetooth SPP SERVER is cv-argus's own
sender.server.SenderServer -- the piece src/esp32-argus's firmware connects to as a *client*.
The protocol is v2 (docs/designs/final-tweaks-contracts.md C1, src/cv-argus/src/sender/
protocol.py): `PULL n` / `DLQ 1` sessions that end with `END <dlq_waiting> <outbox_waiting>`,
then report lines (`ACK` -> `ACKED n`, `TAKEN` -> `LEASED n`, `FAIL <id> <reason> <body|->`,
no reply) and `DONE` -> `OK acked= failed= leased= dlq= outbox=`. Real Bluetooth hardware isn't
used here: the script runs the real SenderServer against cv-argus's own in-memory
FakeTransport/FakeListener pair -- the exact stand-in cv-argus's own unit tests use -- and plays
the ESP32's part by hand, printing every step. It walks one critical alert through the whole
v2 life the firmware gives it when the backend is down and then back:

  1. PULL: a keepalive (ACKed at once) and a critical raise (TAKEN: leased to the ESP32).
  2. A report session: `FAIL <id> no_network <event JSON>` -- the live window ran out, so the
     critical moves to the Pi's dead-letter queue (attempt 1, its composed body kept).
  3. DLQ 1: the one dead letter comes back with its `dlq` block; the ESP32 TAKENs it.
  4. A report session: `ACK <id>` -- delivered on the replay. Nothing is left unsent.
  5. A medium alert the ESP32 TAKENs and then gives up on (its 30 s live window ran out):
     `FAIL <id> retry_window -` moves it straight to the archive (the DLQ holds criticals only).

This proves the real server/buffer/alerts/protocol code works end to end; it does NOT prove
real Bluetooth connectivity, which needs real hardware.

No install needed, no Docker needed. `alerts/`, `buffer/`, and `sender/` are deliberately
dependency-light (pure stdlib), so rather than `pip install -e src/cv-argus` (which would pull in
the full heavy dependency list for nothing), this script registers `cv_argus` as a module pointing
straight at src/cv-argus/src/ itself, below -- the same `package_dir={"cv_argus": "src"}` mapping
setup.py does for a real install, just done at runtime via importlib. A plain `sys.path.insert`
alone wouldn't work: that directory is named `src` on disk, not `cv_argus`, so Python wouldn't
find anything under the name this code's relative imports (`from ..alerts import ...` etc.)
expect.

Usage:
    python src/tests/smoke_cv_argus.py
"""
from __future__ import annotations

import importlib.util
import json
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
    from cv_argus.sender import FakeListener, FakeTransport, SenderServer
    from cv_argus.sender import protocol
except ImportError as exc:
    print(f"[FAIL] cv_argus isn't importable even from {_CV_ARGUS_SRC}: {exc}")
    print()
    print(f"Check that this path actually exists and looks right: {_CV_ARGUS_SRC}")
    print("(this script expects src/tests/ and src/cv-argus/src/ to be siblings under src/ --")
    print(" if the repo got reorganized, update _CV_ARGUS_SRC above to match)")
    sys.exit(1)

_FAILURES: list[str] = []
_TIMEOUT_S = 3.0


def _log(ok: bool, label: str, detail: str = "") -> None:
    tag = "[ OK ]" if ok else "[FAIL]"
    print(f"{tag} {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        _FAILURES.append(label)


def _record(severity: str, previous: str, *, transition: bool, episode: str | None) -> Alert:
    """A v2 drowsiness record as the orchestrator writes it (contracts C1.4)."""
    alerting = severity in ("medium", "critical")
    payload = {
        "class_name": "Drowsy" if alerting else "Not Drowsy",
        "probabilities": [0.05, 0.95] if alerting else [0.9, 0.1],
        "transition": transition,
        "severity": severity,
        "previous_severity": previous,
        "reason": {"critical": "eyes_closed", "medium": "yawning"}.get(severity, "ok"),
        "model": "parts-rules-v1",
    }
    if episode:
        payload["episode"] = episode
    return Alert.new(
        AlertKind.DROWSINESS,
        level=2 if alerting else 1,
        source_id="smoke-cam-0",
        payload=payload,
    )


class _Session:
    """One ESP32 connection: a fresh FakeTransport pair offered to the server's accept loop (the
    firmware opens a new connection per session, and the Pi closes it after `OK`)."""

    def __init__(self, listener: FakeListener) -> None:
        self.client, server_side = FakeTransport.pair()
        listener.offer(server_side)

    def send(self, line: str) -> None:
        self.client.write((line + "\n").encode())
        print(f"       client -> server: {line if len(line) < 120 else line[:117] + '...'}")

    def recv(self) -> str | None:
        line = self.client.readline(timeout=_TIMEOUT_S)
        if line is None:
            return None
        text = line.decode()
        print(f"       client <- server: {text if len(text) < 120 else text[:117] + '...'}")
        return text

    def read_until_end(self) -> tuple[list[dict], tuple[int, int] | None]:
        """The records of a PULL/DLQ session and the parsed `END <dlq> <outbox>`."""
        records: list[dict] = []
        while True:
            text = self.recv()
            if text is None:
                return records, None
            end = protocol.parse_end(text)
            if end is not None:
                return records, end
            records.append(json.loads(text))

    def done(self) -> dict[str, int] | None:
        """`DONE` -> the parsed `OK acked= failed= leased= dlq= outbox=`, then the Pi closes."""
        self.send(protocol.DONE_LINE)
        text = self.recv()
        result = protocol.parse_result(text) if text is not None else None
        closed = self.client.readline(timeout=_TIMEOUT_S) is None
        _log(closed, "the Pi closed the connection after OK")
        return result


def main() -> int:
    print(
        "cv-argus sender/ smoke check, protocol v2 (real server code, simulated transport -- "
        "not real Bluetooth)\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        buffer = open_buffer(Path(tmp) / "smoke_buffer.sqlite3")

        keepalive = _record("none", "none", transition=False, episode=None)
        critical = _record("critical", "none", transition=True, episode="a1b2c3d4e5f6")
        buffer.enqueue(keepalive)
        buffer.enqueue(critical)
        _log(True, "seeded a keepalive and a critical raise into a real Buffer",
             f"critical id={critical.id}")

        listener = FakeListener()
        server = SenderServer(buffer, listener, accept_timeout=0.1)
        server.start()
        _log(True, "started a real SenderServer (accept loop running on its own thread)")

        try:
            # 1. PULL: ACK the keepalive, TAKEN the critical.
            print("\n-- 1. live pull --")
            s = _Session(listener)
            s.send(protocol.format_pull(8))
            records, end = s.read_until_end()
            ids = [r.get("id") for r in records]
            _log(ids == [keepalive.id, critical.id], "PULL served both rows, oldest first",
                 f"got {len(records)} record(s)")
            _log(end == (0, 2), "END <dlq_waiting> <outbox_waiting> counts the served rows",
                 f"got {end}")
            served = {r.get("id"): r for r in records}
            age = served.get(critical.id, {}).get("age_s")
            _log(isinstance(age, (int, float)) and age >= 0, "the record carries age_s",
                 f"age_s={age!r}")
            _log(served.get(critical.id, {}).get("payload", {}).get("severity") == "critical",
                 "the critical record carries its severity")
            s.send(protocol.format_ack([keepalive.id]))
            _log(s.recv() == protocol.format_acked(1), "ACK the keepalive -> ACKED 1")
            s.send(protocol.format_taken([critical.id]))
            _log(s.recv() == protocol.format_leased(1), "TAKEN the critical -> LEASED 1")
            result = s.done()
            _log(result == {"acked": 1, "failed": 0, "leased": 1, "dlq": 0, "outbox": 0},
                 "OK: 1 acked, 1 leased, nothing waiting (the leased row isn't counted)",
                 f"got {result}")

            # 2. A report session: the live window ran out with the backend down.
            print("\n-- 2. report session: the critical is dead-lettered --")
            body = {
                "client_event_id": critical.id, "episode": "a1b2c3d4e5f6", "kind": "alert",
                "severity": "critical", "alert_type": "eyes_closed", "age_s": 61.2,
                "delivery": "live", "queued_seconds": 0,
                "position": {"lat": 20.6597, "lon": -103.3496, "last_known": False,
                             "location_age_s": 0},
                "speed": 62.5, "ai_metadata": None,
            }
            s = _Session(listener)
            s.send(protocol.format_fail(critical.id, "no_network", body))
            result = s.done()
            _log(result == {"acked": 0, "failed": 1, "leased": 0, "dlq": 1, "outbox": 0},
                 "OK: FAIL counted, the critical now waits in the DLQ", f"got {result}")

            # 3. DLQ 1: the dead letter comes back with its dlq block.
            print("\n-- 3. dead-letter pull --")
            s = _Session(listener)
            s.send(protocol.format_dlq(1))
            records, end = s.read_until_end()
            _log(len(records) == 1 and records[0].get("id") == critical.id,
                 "DLQ 1 served the one dead letter", f"got {len(records)} record(s)")
            dlq = records[0].get("dlq", {}) if records else {}
            _log(dlq.get("attempts") == 1 and dlq.get("error") == "no_network",
                 "its dlq block counts the live failure as attempt 1", f"dlq={dlq}")
            _log((dlq.get("body") or {}).get("position") == body["position"],
                 "the composed body kept the event-time position")
            _log(isinstance(dlq.get("queued_s"), int), "dlq.queued_s is an int",
                 f"queued_s={dlq.get('queued_s')!r}")
            _log(end == (1, 0), "END counts the just-served row (not leased yet)", f"got {end}")
            s.send(protocol.format_taken([critical.id]))
            _log(s.recv() == protocol.format_leased(1), "TAKEN the dead letter -> LEASED 1")
            result = s.done()
            _log(result == {"acked": 0, "failed": 0, "leased": 1, "dlq": 0, "outbox": 0},
                 "OK: leased, nothing waiting", f"got {result}")

            # 4. A report session: the replay was delivered.
            print("\n-- 4. report session: delivered on the replay --")
            s = _Session(listener)
            s.send(protocol.format_ack([critical.id]))
            _log(s.recv() == protocol.format_acked(1), "ACK the replay -> ACKED 1")
            result = s.done()
            _log(result == {"acked": 1, "failed": 0, "leased": 0, "dlq": 0, "outbox": 0},
                 "OK: delivered, nothing waiting", f"got {result}")

            # 5. A medium the ESP32 gave up on: archived at once, never dead-lettered.
            print("\n-- 5. a medium alert past its live window is archived --")
            medium = _record("medium", "none", transition=True, episode="0f0e0d0c0b0a")
            buffer.enqueue(medium)
            s = _Session(listener)
            s.send(protocol.format_pull(8))
            records, end = s.read_until_end()
            _log([r.get("id") for r in records] == [medium.id], "PULL served the medium",
                 f"got {len(records)} record(s)")
            _log(end == (0, 1), "END counts it in the outbox", f"got {end}")
            s.send(protocol.format_taken([medium.id]))
            _log(s.recv() == protocol.format_leased(1), "TAKEN the medium -> LEASED 1")
            result = s.done()
            _log(result == {"acked": 0, "failed": 0, "leased": 1, "dlq": 0, "outbox": 0},
                 "OK: leased, nothing waiting", f"got {result}")
            s = _Session(listener)
            s.send(protocol.format_fail(medium.id, "retry_window", None))
            result = s.done()
            _log(result == {"acked": 0, "failed": 1, "leased": 0, "dlq": 0, "outbox": 0},
                 "OK: FAIL counted, the medium did not go to the DLQ", f"got {result}")
        finally:
            server.stop()
            server.join(timeout=2.0)
            _log(not server.is_alive, "server stopped cleanly")

        counts = buffer.counts()
        _log(buffer.unsent_count() == 0, "buffer shows 0 unsent after the round trips")
        _log(counts.get("archived") == 1
             and counts.get("archive_reasons") == {"retry_window": 1},
             "only the medium was archived, with reason retry_window", f"counts={counts}")
        buffer.close()

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print(
        "All checks passed -- the real sender/buffer/alerts/protocol v2 code works end to end "
        "(simulated transport, not real Bluetooth hardware)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
