"""Bench test against the REAL ESP32: this machine plays the Pi and the cloud at once.

    cv-argus Buffer + SenderServer (real code) --Bluetooth SPP--> ESP32 (real firmware)
        --WiFi/HTTP, device key--> backend-argus (real app, in memory, see bench_backend.py)

It feeds the Pi's buffer the records cv-argus produces (Drowsy readings + route_status
heartbeats), logs every protocol line the ESP32 exchanges, and watches the backend for what the
ESP32 relays. Prints [ OK ]/[FAIL] per check and exits 0 only if all of them pass.

    python src/esp32-argus/test/hil/esp32_hil.py --serial COM5     # Windows incoming SPP COM port
    python src/esp32-argus/test/hil/esp32_hil.py --rfcomm 4     # Linux/BlueZ (e.g. the real Pi)

See README.md next to this file for the Bluetooth pairing and config.h setup.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import _imports  # noqa: F401  (path wiring, must come first)

import httpx

from bench_backend import BenchBackend, ROUTE_ID, config_h_values
from cv_argus.alerts import Alert, AlertKind, route_status
from cv_argus.buffer import open_buffer
from cv_argus.sender import SenderServer
from cv_argus.sender.transport import Listener, Transport

ROOT_ADMIN = {
    "email": "admin@argus.dev",
    # sha256("changeme123"): what ui-argus sends, see smoke_backend.py's sha256_hex()
    "password": "494a715f7e9b4071aca61bac42ca858a309524e5864f0920030862a4ae7589be",
}


def drowsy(p_drowsy: float = 0.88) -> Alert:
    """What cv-argus's Orchestrator enqueues for a Drowsy detection (same as edge_chain)."""
    return Alert.new(
        kind=AlertKind.DROWSINESS,
        level=2,
        source_id="cam-0",
        payload={"class_name": "Drowsy", "probabilities": [round(1 - p_drowsy, 4), p_drowsy]},
    )


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


class SerialSppListener(Listener):
    """Windows' "incoming" Bluetooth COM port as a cv-argus `Listener`. Windows owns the RFCOMM
    socket and advertises the SPP service, so the ESP32's `SerialBT.connect(mac)` finds it. A COM
    port has no accept event, so a session starts when the first byte (the PULL) arrives."""

    def __init__(self, port_name: str) -> None:
        import serial
        from serial.tools import list_ports

        try:
            self._port = serial.Serial(port_name, 115200, timeout=0.1)
        except serial.SerialException as exc:
            ports = ", ".join(f"{p.device} ({p.description})" for p in list_ports.comports())
            raise SystemExit(
                f"\nCan't open {port_name}: {exc}\n"
                f"COM ports on this PC: {ports or 'none'}\n"
                "Use the *incoming Bluetooth* COM port, not the USB one. If none is listed, create\n"
                "it: Bluetooth settings > More Bluetooth options > COM Ports > Add > Incoming.\n"
                "See README.md, setup step 4."
            ) from None

    def accept(self, timeout: float | None = None) -> Transport | None:
        deadline = time.monotonic() + (timeout or 0)
        while self._port.in_waiting == 0:
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)
        return _SerialSession(self._port)

    def close(self) -> None:
        self._port.close()


class LoggingTransport(Transport):
    """Prints every line in both directions, and counts PULLs/ACKs for the final checks."""

    pulls = 0
    acks = 0

    def __init__(self, inner: Transport) -> None:
        self._inner = inner

    def readline(self, timeout: float | None = None) -> bytes | None:
        line = self._inner.readline(timeout)
        if line is not None:
            text = line.decode(errors="replace")
            print(f"  [bt] ESP32 -> PC: {text}")
            if text.startswith("PULL"):
                LoggingTransport.pulls += 1
            elif text.startswith("ACK"):
                LoggingTransport.acks += 1
        return line

    def write(self, data: bytes) -> None:
        for line in data.decode(errors="replace").splitlines():
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
        return SerialSppListener(args.serial)
    from cv_argus.sender import BluetoothSppListener

    return BluetoothSppListener(channel=args.rfcomm)


# --- Run ----------------------------------------------------------------------------------


def _log(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{' OK ' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--serial", help="Windows incoming Bluetooth COM port, e.g. COM5")
    where.add_argument("--rfcomm", type=int, help="Linux/BlueZ RFCOMM channel, e.g. 4")
    parser.add_argument("--duration", type=float, default=120.0, help="seconds to run (max)")
    parser.add_argument("--drowsy-every", type=float, default=1.0, help="seconds per Drowsy record")
    parser.add_argument("--heartbeat-every", type=float, default=10.0, help="seconds per route_status")
    args = parser.parse_args()

    backend = BenchBackend()
    backend.start()
    print("Bench backend up. The firmware's config.h must have:\n")
    print(config_h_values())
    print()

    buffer = open_buffer(Path(tempfile.mkdtemp(prefix="argus-hil-")) / "buffer.sqlite3")
    server = SenderServer(buffer, LoggingListener(make_listener(args)), accept_timeout=0.5)
    server.start()

    http = httpx.Client(base_url=backend.local_url, timeout=5.0)
    token = http.post("/api/auth/login", json=ROOT_ADMIN).json()["access_token"]
    http.headers["Authorization"] = f"Bearer {token}"

    print(f"Feeding the Pi buffer for up to {args.duration:.0f}s. Waiting for the ESP32...\n")
    enqueued = 0
    seen_alerts: set[str] = set()
    status = None
    start = last_drowsy = last_heartbeat = time.monotonic()
    buffer.enqueue(route_status("cam-0"))
    enqueued += 1
    try:
        while time.monotonic() - start < args.duration:
            now = time.monotonic()
            if now - last_drowsy >= args.drowsy_every:
                buffer.enqueue(drowsy())
                enqueued += 1
                last_drowsy = now
            if now - last_heartbeat >= args.heartbeat_every:
                buffer.enqueue(route_status("cam-0"))
                enqueued += 1
                last_heartbeat = now

            for alert in http.get("/api/alerts", params={"route_id": ROUTE_ID}).json():
                if alert["id_alert"] not in seen_alerts:
                    seen_alerts.add(alert["id_alert"])
                    print(
                        f"  [http] alert from ESP32: {alert['alert_type']} "
                        f"severity={alert['severity_level']} source={alert['source']} "
                        f"grip={alert['grip_status']} at {alert['coordinates']}"
                    )
            resp = http.get(f"/api/routes/{ROUTE_ID}/status")
            if resp.status_code == 200 and resp.json() != status:
                status = resp.json()
                print(
                    f"  [http] route status from ESP32: vigilance={status['vigilance']} "
                    f"at {status['current_coordinates']}"
                )

            if seen_alerts and status is not None and LoggingTransport.acks:
                break  # everything the checks need has happened
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        unsent = buffer.unsent_count()
        server.stop()
        server.join(timeout=3.0)
        buffer.close()
        http.close()
        backend.stop()

    print()
    results = [
        _log(LoggingTransport.pulls > 0, "ESP32 connected over Bluetooth and sent PULL",
             f"{LoggingTransport.pulls} pull(s)"),
        _log(LoggingTransport.acks > 0, "ESP32 ACKed relayed records",
             f"{enqueued - unsent}/{enqueued} records left the Pi buffer"),
        _log(status is not None, "route status reached the backend over HTTP (device key)"),
        _log(bool(seen_alerts), "fusion alert reached the backend over HTTP (device key)",
             f"{len(seen_alerts)} alert(s)"),
    ]
    if not all(results):
        print("\nSee README.md's troubleshooting table, and the ESP32's Serial Monitor (115200).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
