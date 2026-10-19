"""A Python stand-in for the ESP32 firmware's relay loop, for the edge-chain tests.

Mirrors `src/esp32-argus/src/esp32_argus/`'s `bluetoothPullAlerts()` (bluetooth_client.cpp)
and `handlePiRecord()` (esp32_argus.ino) step for step, but drives the *real* Python pieces on
either side of it:

- the Pi side is cv-argus's real `SenderServer` + `Buffer`, reached over `FakeTransport`
  (the radio is the only simulated part);
- the decision logic is cv-argus's real `FusionOrchestrator` (`fusion_contract.py`), which the
  firmware's `fusion.cpp` is a 1:1 port of;
- the cloud side is backend-argus's real FastAPI app, called with a real per-truck device key.

GPS and the grip sensor are injected as plain values, since those are hardware readings.
Keep this in sync with the firmware by hand if either changes; there's no shared code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from httpx import AsyncClient

from cv_argus.orchestrator.fusion_contract import (
    DrowsyState,
    FusionOrchestrator,
    FusionReading,
    GripState,
    Severity,
)
from cv_argus.sender import END_LINE, FakeListener, FakeTransport, format_ack, format_pull

# config.h's DROWSY_PROBABILITY_THRESHOLD, itself cv-argus's FUSED_MODEL_THRESHOLD.
DROWSY_PROBABILITY_THRESHOLD = 0.57
PI_BT_PULL_COUNT = 50
PI_BT_LINE_TIMEOUT_S = 3.0


def _reject_constant(token: str):
    raise ValueError(f"ArduinoJson rejects the token {token}")


def parse_record_line(text: str) -> dict | None:
    """bluetooth_client.cpp's parseRecordLine(): None for what the firmware can't parse, which it
    then never ACKs. ArduinoJson 7 (no ARDUINOJSON_ENABLE_NAN) fails the whole line with
    InvalidInput on a bare NaN/Infinity token, which Python's json.dumps writes by default
    (REC-19). An unknown `kind` is rejected too (EBT-03)."""
    try:
        record = json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("kind") not in ("drowsiness", "route_status"):
        return None
    return record


@dataclass
class PollResult:
    pulled: int
    acked: list[str]
    protocol_ok: bool


@dataclass
class SimulatedEsp32:
    listener: FakeListener
    http: AsyncClient
    device_key: str
    route_id: str
    clock: Callable[[], float]  # monotonic seconds, stands in for millis() / 1000
    grip: GripState = GripState.GOOD
    gps: tuple[float, float, float] | None = (19.43, -99.13, 72.0)  # lat, lon, km/h
    fusion: FusionOrchestrator = field(default_factory=FusionOrchestrator)
    # fusion.cpp's confirmedSeverity(); the Python contract has no accessor, and every
    # FusionDecision.severity *is* the confirmed severity as of that tick.
    confirmed_severity: Severity = Severity.LOW
    wifi: bool = True  # wifiIsConnected()
    # Faults a test injects. drop_next_ack: the link drops after the batch is relayed, before
    # the ACK is sent. status_failures: that many route-status POSTs fail the way a 5xx would.
    drop_next_ack: bool = False
    status_failures: int = 0
    # What reached the backend, stamped with this device's clock (for timing assertions).
    alert_posts: list[tuple[float, dict]] = field(default_factory=list)
    status_posts: list[tuple[float, dict]] = field(default_factory=list)

    def tick(self) -> None:
        """loop()'s work between Bluetooth polls. The firmware has no local grip tick yet: fusion
        only runs when a Pi record arrives (FUS-01), so there is nothing to do here."""

    # --- bluetooth_client.cpp: bluetoothPullAlerts() -----------------------------------
    async def poll(self, *, half_open_before_ack: bool = False) -> PollResult:
        """One bluetoothPullAlerts(). `half_open_before_ack` models an ESP32 reset right after
        END: the batch was relayed, no ACK is sent and the link is never closed, so the Pi only
        notices when its own ACK wait runs out."""
        client, server_side = FakeTransport.pair()
        self.listener.offer(server_side)  # SerialBT.connect(piMac)

        client.write((format_pull(PI_BT_PULL_COUNT) + "\n").encode())
        ack_ids: list[str] = []
        pulled = 0
        protocol_ok = True
        while True:
            line = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
            if line is None:
                protocol_ok = False
                break
            text = line.decode()
            if text == END_LINE:
                break
            pulled += 1
            record = parse_record_line(text)
            if record is not None and await self.handle_pi_record(record):
                ack_ids.append(record["id"])

        if half_open_before_ack:
            return PollResult(pulled=pulled, acked=[], protocol_ok=False)
        if self.drop_next_ack and ack_ids:
            self.drop_next_ack = False
            client.close()
            return PollResult(pulled=pulled, acked=[], protocol_ok=False)
        if protocol_ok and ack_ids:
            client.write((format_ack(ack_ids) + "\n").encode())
            acked = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
            if acked is None or not acked.decode().startswith("ACKED"):
                protocol_ok = False
        client.close()  # SerialBT.disconnect()
        return PollResult(pulled=pulled, acked=ack_ids, protocol_ok=protocol_ok)

    # --- esp32_argus.ino: handlePiRecord() ----------------------------------------------
    async def handle_pi_record(self, record: dict) -> bool:
        """True iff the record was relayed onward, which is what gates its ACK."""
        if self.gps is None:
            return False  # no GPS fix: don't ACK, the Pi re-serves it later
        if not self.wifi:
            return False  # "WiFi down -- not relaying"
        lat, lon, speed = self.gps

        if record["kind"] == "route_status":
            return await self._post_route_status(lat, lon, speed)

        payload = record.get("payload") or {}
        # parseRecordLine(): both stay 0.0f unless the array has exactly two entries, so a
        # No Face row (probabilities: null) reads as p(Drowsy) = 0, i.e. Not Drowsy (CV-04).
        probabilities = payload.get("probabilities")
        p_not_drowsy, p_drowsy = 0.0, 0.0
        if isinstance(probabilities, list) and len(probabilities) == 2:
            p_not_drowsy, p_drowsy = probabilities[0], probabilities[1]
        # ArduinoJson's as<String>() of a missing or null field is the text "null" (EBT-08).
        class_name = payload.get("class_name")
        class_name = "null" if class_name is None else str(class_name)
        drowsy = (
            DrowsyState.DROWSY
            if p_drowsy >= DROWSY_PROBABILITY_THRESHOLD
            else DrowsyState.NOT_DROWSY
        )
        decision = self.fusion.update(FusionReading(drowsy=drowsy, grip=self.grip, at=self.clock()))
        self.confirmed_severity = decision.severity

        ok = True  # nothing to post this tick still counts as processed
        if decision.alert is not None:
            body = {
                "id_route": self.route_id,
                "alert_type": class_name,
                "severity_level": decision.alert.severity_level.value,
                "source": "fusion",
                "ai_metadata": {"scores": {"not_drowsy": p_not_drowsy, "drowsy": p_drowsy}},
                "grip_status": decision.alert.grip_status.value,
                "coordinates": {"lat": lat, "lon": lon},
                "speed_at_event": speed,
            }
            if decision.alert.related_alert_id is not None:
                body["related_alert_id"] = decision.alert.related_alert_id
            resp = await self.http.post("/api/alerts", json=body, headers=self._headers())
            ok = resp.status_code == 201
            if ok:
                self.alert_posts.append((self.clock(), body))
                self.fusion.record_alert_posted(resp.json()["id_alert"])

        if decision.resolved_incident_id is not None:
            resolved_at = datetime.now(timezone.utc).isoformat()  # gpsUtcIso8601() on device
            resp = await self.http.put(
                f"/api/alerts/{decision.resolved_incident_id}",
                json={"resolved_at": resolved_at},
                headers=self._headers(),
            )
            ok = resp.status_code == 200 and ok
        return ok

    async def _post_route_status(self, lat: float, lon: float, speed: float) -> bool:
        if self.status_failures > 0:
            self.status_failures -= 1
            return False  # as if the backend answered 5xx
        body = {
            "current_coordinates": {"lat": lat, "lon": lon},
            "current_speed": speed,
            "odometer": 0.0,  # no odometer sensor on the device, see config.h
            "vigilance": self.confirmed_severity.value,
        }
        resp = await self.http.post(
            f"/api/routes/{self.route_id}/status", json=body, headers=self._headers()
        )
        if resp.status_code == 201:
            self.status_posts.append((self.clock(), body))
        return resp.status_code == 201

    def _headers(self) -> dict[str, str]:
        return {"X-Device-Api-Key": self.device_key}
