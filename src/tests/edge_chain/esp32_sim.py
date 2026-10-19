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
from cv_argus.sender import (
    CONFIG_REQUEST,
    END_LINE,
    NOCONFIG_LINE,
    FakeListener,
    FakeTransport,
    format_ack,
    format_pull,
    parse_config,
)

# config.h's DROWSY_PROBABILITY_THRESHOLD, itself cv-argus's FUSED_MODEL_THRESHOLD.
DROWSY_PROBABILITY_THRESHOLD = 0.57
PI_BT_PULL_COUNT = 50
PI_BT_LINE_TIMEOUT_S = 3.0


def fetch_config(listener: FakeListener) -> dict[str, str] | str | None:
    """bluetooth_client.cpp's bluetoothFetchConfig() + device_config.cpp's parseConfigLine():
    the credentials dict, `NOCONFIG_LINE` when the Pi has none, or `None` on a failure."""
    client, server_side = FakeTransport.pair()
    listener.offer(server_side)
    client.write((CONFIG_REQUEST + "\n").encode())
    line = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
    client.close()
    if line is None:
        return None
    text = line.decode()
    if text == NOCONFIG_LINE:
        return NOCONFIG_LINE
    return parse_config(text)


async def resolve_active_route(http: AsyncClient, device_key: str) -> str | None:
    """backend_api.cpp's ensureActiveRoute(): the truck's route id from its key alone."""
    resp = await http.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": device_key}
    )
    return resp.json()["id_route"] if resp.status_code == 200 else None


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

    # --- bluetooth_client.cpp: bluetoothPullAlerts() -----------------------------------
    async def poll(self) -> PollResult:
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
            record = json.loads(text)
            if await self.handle_pi_record(record):
                ack_ids.append(record["id"])

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
        lat, lon, speed = self.gps

        if record["kind"] == "route_status":
            return await self._post_route_status(lat, lon, speed)

        probabilities = record["payload"]["probabilities"]
        p_not_drowsy, p_drowsy = probabilities[0], probabilities[1]
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
                "alert_type": record["payload"]["class_name"],
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
        resp = await self.http.post(
            f"/api/routes/{self.route_id}/status",
            json={
                "current_coordinates": {"lat": lat, "lon": lon},
                "current_speed": speed,
                "odometer": 0.0,  # no odometer sensor on the device, see config.h
                "vigilance": self.confirmed_severity.value,
            },
            headers=self._headers(),
        )
        return resp.status_code == 201

    def _headers(self) -> dict[str, str]:
        return {"X-Device-Api-Key": self.device_key}
