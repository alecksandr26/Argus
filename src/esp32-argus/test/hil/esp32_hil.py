"""Bench test against the REAL ESP32: this machine plays the Pi and the cloud at once.

    cv-argus Buffer + SenderServer (real code) --Bluetooth SPP v2--> ESP32 (real firmware)
        --WiFi, POST /api/devices/me/report, device key--> backend-argus (real app, in memory,
        see bench_backend.py)

It feeds the Pi's buffer what cv-argus's orchestrator produces under protocol v2
(docs/designs/final-tweaks-contracts.md C1.4/C1.5): a keepalive every 2 s, one CRITICAL raise
(an event) after --alert-after seconds, keepalives while it is held, then the recovery (a driver
OK) after --recover-after more seconds. It logs every protocol line the ESP32 exchanges and
watches the backend for what the ESP32 reports. Prints [ OK ]/[FAIL] per check and exits 0 only
if all of them pass.

    python src/esp32-argus/test/hil/esp32_hil.py --serial auto     # Windows incoming SPP COM port(s)
    python src/esp32-argus/test/hil/esp32_hil.py --rfcomm 4     # Linux/BlueZ (e.g. the real Pi)

See README.md next to this file for the Bluetooth pairing and config.h setup.
"""
from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
import uuid
from pathlib import Path

import _imports  # noqa: F401  (path wiring, must come first)

import httpx

from bench_backend import BenchBackend, ROUTE_ID, TRUCK_ID, bench_device_config
from cv_argus.alerts import Alert, AlertKind
from cv_argus.buffer import open_buffer
from cv_argus.sender import SenderServer
from cv_argus.sender.protocol import parse_config
from cv_argus.sender.transport import Listener, Transport

ROOT_ADMIN = {
    "email": "admin@argus.dev",
    # sha256("changeme123"): what ui-argus sends, see smoke_backend.py's sha256_hex()
    "password": "494a715f7e9b4071aca61bac42ca858a309524e5864f0920030862a4ae7589be",
}


# config.h's BENCH_GPS_LAT/LON: what the ESP32 sends with BENCH_FAKE_GPS 1 instead of the NEO-6M.
BENCH_FAKE_POSITION = (20.6597, -103.3496)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle (haversine) distance in metres between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def parse_lat_lon(text: str) -> tuple[float, float]:
    lat, lon = (float(v) for v in text.replace(" ", "").split(","))
    return lat, lon


class PiScript:
    """What cv-argus's orchestrator enqueues for one incident, as v2 records: keepalives
    (transition false) every `keepalive_every` s, a none -> critical raise after `alert_after` s,
    and the critical -> none recovery (a driver OK, same episode) `recover_after` s later."""

    def __init__(self, alert_after: float, recover_after: float, keepalive_every: float = 2.0):
        self.alert_after = alert_after
        self.recover_after = recover_after
        self.keepalive_every = keepalive_every
        self.episode = uuid.uuid4().hex[:12]
        self.severity = "none"
        self.raised_id: str | None = None
        self.ok_id: str | None = None
        self._last_keepalive: float | None = None

    @staticmethod
    def _record(severity: str, previous: str, transition: bool, episode: str | None) -> Alert:
        drowsy = severity in ("medium", "critical")
        payload = {
            "class_name": "Drowsy" if drowsy else "Not Drowsy",
            "probabilities": [0.05, 0.95] if drowsy else [0.9, 0.1],
            "transition": transition,
            "severity": severity,
            "previous_severity": previous,
            "reason": "eyes_closed" if drowsy else "ok",
            "model": "parts-rules-v1",
        }
        if episode:
            payload["episode"] = episode
        return Alert.new(kind=AlertKind.DROWSINESS, level=2 if drowsy else 1, source_id="cam-0",
                         payload=payload)

    def tick(self, elapsed: float) -> list[Alert]:
        out: list[Alert] = []
        if self.raised_id is None and elapsed >= self.alert_after:
            alert = self._record("critical", "none", True, self.episode)
            self.raised_id, self.severity = alert.id, "critical"
            out.append(alert)
        elif (self.raised_id is not None and self.ok_id is None
              and elapsed >= self.alert_after + self.recover_after):
            # The recovery record carries the episode too (C1.4), then it ends.
            alert = self._record("none", "critical", True, self.episode)
            self.ok_id, self.severity = alert.id, "none"
            out.append(alert)
        if self._last_keepalive is None or elapsed - self._last_keepalive >= self.keepalive_every:
            self._last_keepalive = elapsed
            held = self.episode if self.severity == "critical" else None
            out.append(self._record(self.severity, self.severity, False, held))
        return out


# --- Transports ---------------------------------------------------------------------------


class _SerialSession(Transport):
    """One ESP32 connection over a COM port. `close()` leaves the port open for the next one."""

    def __init__(self, port) -> None:
        self._port = port
        self._buf = b""

    def readline(self, timeout: float | None = None) -> bytes | None:
        import serial

        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self._buf:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            try:
                self._buf += self._port.read(self._port.in_waiting or 1)
            except serial.SerialException:
                return None
        line, _, self._buf = self._buf.partition(b"\n")
        return line.rstrip(b"\r")

    def write(self, data: bytes) -> None:
        self._port.write(data)
        self._port.flush()

    def close(self) -> None:
        pass


def incoming_bt_ports() -> list[str]:
    """Windows' incoming Bluetooth COM ports: SPP (UUID 1101) with no remote device address in
    the hardware id. The outgoing port Windows creates when pairing names the ESP32's MAC
    instead, and opening that one would dial the ESP32, so it's skipped."""
    from serial.tools import list_ports

    return [
        p.device
        for p in list_ports.comports()
        if "00001101-0000-1000-8000-00805F9B34FB" in p.hwid.upper()
        and "LOCALMFG&0000" in p.hwid.upper()
    ]


class SerialSppListener(Listener):
    """Windows' "incoming" Bluetooth COM ports as a cv-argus `Listener`. Windows owns the RFCOMM
    socket and advertises the SPP service, so the ESP32's `SerialBT.connect(mac)` finds it, but
    only while a program holds the port open. With more than one incoming port Windows
    advertises them all and the ESP32 takes whichever it finds first, so this listens on every
    one given. A COM port has no accept event, so a session starts when the first byte (the
    CONFIG, PULL or DLQ) arrives."""

    def __init__(self, port_names: list[str]) -> None:
        import serial
        from serial.tools import list_ports

        self._ports = []
        for name in port_names:
            try:
                self._ports.append(serial.Serial(name, 115200, timeout=0.1))
            except serial.SerialException as exc:
                ports = ", ".join(f"{p.device} ({p.description})" for p in list_ports.comports())
                raise SystemExit(
                    f"\nCan't open {name}: {exc}\n"
                    f"COM ports on this PC: {ports or 'none'}\n"
                    "Use the *incoming Bluetooth* COM port, not the USB one. If none is listed,\n"
                    "create it: Bluetooth settings > More Bluetooth options > COM Ports > Add >\n"
                    "Incoming. See README.md, setup step 5."
                ) from None
        print(f"Listening on {', '.join(port_names)}")

    def accept(self, timeout: float | None = None) -> Transport | None:
        deadline = time.monotonic() + (timeout or 0)
        while True:
            for port in self._ports:
                if port.in_waiting:
                    return _SerialSession(port)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    def close(self) -> None:
        for port in self._ports:
            port.close()


class LoggingTransport(Transport):
    """Prints every line in both directions, and counts CONFIG/PULL/ACK/TAKEN for the final checks.
    The device key in a CONFIG reply is masked."""

    configs = 0
    pulls = 0
    acks = 0
    taken = 0

    def __init__(self, inner: Transport) -> None:
        self._inner = inner

    def readline(self, timeout: float | None = None) -> bytes | None:
        line = self._inner.readline(timeout)
        if line is not None:
            text = line.decode(errors="replace")
            print(f"  [bt] ESP32 -> PC: {text}")
            if text == "CONFIG":
                LoggingTransport.configs += 1
            elif text.startswith("PULL"):
                LoggingTransport.pulls += 1
            elif text.startswith("ACK"):
                LoggingTransport.acks += 1
            elif text.startswith("TAKEN"):
                LoggingTransport.taken += 1
        return line

    def write(self, data: bytes) -> None:
        for line in data.decode(errors="replace").splitlines():
            config = parse_config(line)
            if config is not None:
                line = f"CONFIG truck_id={config.get('truck_id')} backend_url={config.get('backend_url')} device_api_key=***"
            print(f"  [bt] PC -> ESP32: {line[:100]}{'...' if len(line) > 100 else ''}")
        self._inner.write(data)

    def close(self) -> None:
        self._inner.close()


class LoggingListener(Listener):
    def __init__(self, inner: Listener) -> None:
        self._inner = inner

    def accept(self, timeout: float | None = None) -> Transport | None:
        transport = self._inner.accept(timeout)
        if transport is None:
            return None
        print("  [bt] ESP32 connected")
        return LoggingTransport(transport)

    def close(self) -> None:
        self._inner.close()


def make_listener(args: argparse.Namespace) -> Listener:
    if args.serial:
        names = incoming_bt_ports() if args.serial == "auto" else args.serial.split(",")
        if not names:
            raise SystemExit(
                "No incoming Bluetooth COM port found. Create one: Bluetooth settings > More\n"
                "Bluetooth options > COM Ports > Add > Incoming. See README.md, setup step 5."
            )
        return SerialSppListener(names)
    from cv_argus.sender import BluetoothSppListener

    return BluetoothSppListener(channel=args.rfcomm)


# --- Run ----------------------------------------------------------------------------------


def _log(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{' OK ' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def check_location(
    positions: list[tuple[float, float]], expected: tuple[float, float], tolerance_m: float
) -> bool:
    """Every position the ESP32 posted must come from the real GPS and be near `expected`."""
    if not positions:
        return _log(False, "GPS position matches --expect-location", "the ESP32 sent no positions")
    if any(distance_m(p, BENCH_FAKE_POSITION) < 1.0 for p in positions):
        return _log(
            False,
            "GPS position matches --expect-location",
            "the ESP32 sent the BENCH_FAKE_GPS position; set it to 0 in config.h and reflash",
        )
    worst = max(positions, key=lambda p: distance_m(p, expected))
    worst_m = distance_m(worst, expected)
    return _log(
        worst_m <= tolerance_m,
        "GPS position matches --expect-location",
        f"{len(positions)} position(s), farthest {worst_m:.0f} m away at {worst[0]:.6f},{worst[1]:.6f}"
        f" (tolerance {tolerance_m:.0f} m) https://www.google.com/maps?q={worst[0]},{worst[1]}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--serial",
        help="Windows incoming Bluetooth COM port(s): 'auto' (every incoming one), COM4, or COM4,COM6",
    )
    where.add_argument("--rfcomm", type=int, help="Linux/BlueZ RFCOMM channel, e.g. 4")
    parser.add_argument("--duration", type=float, default=150.0, help="seconds to run (max)")
    parser.add_argument("--alert-after", type=float, default=15.0,
                        help="seconds before the CRITICAL raise (after the ESP32's first PULL)")
    parser.add_argument("--recover-after", type=float, default=20.0,
                        help="seconds the CRITICAL is held before the recovery (driver OK)")
    parser.add_argument(
        "--expect-location",
        type=parse_lat_lon,
        metavar="LAT,LON",
        help="where the ESP32 really is (e.g. right-click > copy in Google Maps); every position "
        "it sends must be within --tolerance-m of it, from the NEO-6M (not BENCH_FAKE_GPS)",
    )
    parser.add_argument("--tolerance-m", type=float, default=100.0, help="for --expect-location")
    args = parser.parse_args()

    backend = BenchBackend()
    backend.start()
    device_config = bench_device_config()
    print(
        f"Bench backend up at {device_config.backend_url}. The ESP32 gets that URL, the bench "
        "device key and the WiFi from this PC over Bluetooth (CONFIG) -- config.h only needs "
        "PI_BT_MAC_ADDRESS and PI_BT_CHANNEL.\n"
    )

    buffer = open_buffer(Path(tempfile.mkdtemp(prefix="argus-hil-")) / "buffer.sqlite3")
    server = SenderServer(
        buffer,
        LoggingListener(make_listener(args)),
        accept_timeout=0.5,
        device_config=device_config,
    )
    server.start()

    http = httpx.Client(base_url=backend.local_url, timeout=5.0)
    token = http.post("/api/auth/login", json=ROOT_ADMIN).json()["access_token"]
    http.headers["Authorization"] = f"Bearer {token}"

    print(f"Feeding the Pi buffer for up to {args.duration:.0f}s. Waiting for the ESP32...\n")
    script = PiScript(args.alert_after, args.recover_after)
    enqueued = 0
    alerts: dict[str, dict] = {}
    positions: list[tuple[float, float]] = []
    points_seen = 0
    last_seen = None
    start = time.monotonic()
    script_start: float | None = None
    try:
        while time.monotonic() - start < args.duration:
            now = time.monotonic()
            # The incident's clock starts once the ESP32 is pulling, so its first CONFIG + WiFi
            # join doesn't eat the alert's live window.
            if script_start is None and LoggingTransport.pulls:
                script_start = now
            for record in script.tick(now - script_start if script_start is not None else 0.0):
                buffer.enqueue(record)
                enqueued += 1
                if record.payload["transition"]:
                    print(f"  [pi] enqueued {record.payload['previous_severity']} -> "
                          f"{record.payload['severity']} ({record.id[:8]}, episode {script.episode})")

            for alert in http.get("/api/alerts", params={"route_id": ROUTE_ID}).json():
                previous = alerts.get(alert["id_alert"])
                alerts[alert["id_alert"]] = alert
                if previous is None:
                    if alert.get("coordinates"):
                        positions.append((alert["coordinates"]["lat"], alert["coordinates"]["lon"]))
                    print(
                        f"  [http] alert from ESP32: {alert['alert_type']} "
                        f"severity={alert['severity_level']} delivery={alert.get('delivery')} "
                        f"at {alert.get('coordinates') or 'position unknown'}"
                        + (" (last known)" if alert.get("location_is_last_known") else "")
                    )
                elif alert.get("driver_ok_at") and not previous.get("driver_ok_at"):
                    print(f"  [http] driver OK for that alert at {alert['driver_ok_at']}")
            track = http.get(f"/api/routes/{ROUTE_ID}/track")
            if track.status_code == 200:
                points = track.json()["points"]
                for p in points[points_seen:]:
                    positions.append((p["lat"], p["lon"]))
                if len(points) > points_seen:
                    p = points[-1]
                    print(f"  [http] {len(points) - points_seen} GPS point(s), newest {p['lat']:.6f},"
                          f"{p['lon']:.6f} vigilance={p['vigilance']}"
                          + (" (last known)" if p["last_known"] else ""))
                points_seen = len(points)
            truck = http.get(f"/api/trucks/{TRUCK_ID}")
            if truck.status_code == 200:
                last_seen = truck.json().get("device_last_seen_at") or last_seen

            raised = [a for a in alerts.values() if a["severity_level"] == "critical"]
            if script.ok_id and raised and raised[0].get("driver_ok_at") and points_seen:
                break  # everything the checks need has happened
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        counts = buffer.counts()
        server.stop()
        server.join(timeout=3.0)
        buffer.close()
        http.close()
        backend.stop()

    critical = [a for a in alerts.values() if a["severity_level"] == "critical"]
    print(f"\nPi buffer at the end: {counts}")
    results = [
        _log(LoggingTransport.configs > 0,
             "ESP32 fetched its backend credentials over Bluetooth (CONFIG)",
             f"{LoggingTransport.configs} request(s)"),
        _log(LoggingTransport.pulls > 0, "ESP32 connected over Bluetooth and sent PULL",
             f"{LoggingTransport.pulls} pull(s)"),
        _log(LoggingTransport.acks > 0 and LoggingTransport.taken > 0,
             "ESP32 ACKed keepalives and TAKEN the events (protocol v2)",
             f"{LoggingTransport.acks} ACK line(s), {LoggingTransport.taken} TAKEN line(s), "
             f"{enqueued} record(s) enqueued"),
        _log(last_seen is not None, "a report reached the backend (device_last_seen_at)",
             f"last seen {last_seen}"),
        _log(points_seen > 0, "GPS points reached the route (needs a fix or BENCH_FAKE_GPS 1)",
             f"{points_seen} point(s)"),
        _log(len(critical) == 1, "exactly one CRITICAL alert for the incident",
             f"{len(critical)} critical, {len(alerts)} alert(s) in all"),
        _log(bool(critical) and bool(critical[0].get("driver_ok_at")),
             "the driver OK reached it (driver_ok_at set; the alert stays open)"),
    ]
    if args.expect_location:
        results.append(check_location(positions, args.expect_location, args.tolerance_m))
    if not all(results):
        print("\nSee README.md's troubleshooting table, and the ESP32's Serial Monitor (115200).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
