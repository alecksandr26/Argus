"""A Python stand-in for the ESP32 firmware's relay loop, for the edge-chain tests.

Mirrors `src/esp32-argus/src/esp32_argus/` step for step -- `bluetoothExchange()`
(bluetooth_client.cpp), `parsePiRecord()` (pi_record.cpp), `decidePiEvent()`
(pi_event_policy.cpp), the compose functions (backend_messages.cpp) and `handle()` /
`loop()` (esp32_argus.ino) -- but drives the *real* Python pieces on either side of it:

- the Pi side is cv-argus's real `SenderServer` + `Buffer`, reached over `FakeTransport`
  (the radio is the only simulated part);
- the cloud side is backend-argus's real FastAPI app, called with a real per-truck device key.

Live first, dead-letter on failure: every record is reported back to the Pi as delivered
(`ACK`) or not (`FAIL <reason> <composed message>`); the Pi keeps only failed ones, and they're
replayed `DLQ_REPLAY_BATCH` at a time once the backend is reachable. There's no grip sensor yet,
so alerts follow the Pi's own severity. GPS is injected as a plain value (a hardware reading).
Keep this in sync with the firmware by hand if either changes; there's no shared code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from httpx import AsyncClient

from cv_argus.sender import (
    CONFIG_REQUEST,
    NOCONFIG_LINE,
    FakeListener,
    FakeTransport,
    format_ack,
    parse_config,
    protocol,
)

PI_BT_PULL_COUNT = 8  # config.h
DLQ_REPLAY_BATCH = 2  # config.h
PI_BT_LINE_TIMEOUT_S = 3.0

_RANK = {"none": 1, "low": 2, "medium": 3, "critical": 4}
_ALERTING = ("medium", "critical")
_CLASSES = ("Drowsy", "Not Drowsy", "No Face")


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


def _reject_constant(token: str):
    raise ValueError(f"ArduinoJson rejects the token {token}")


def parse_record_line(text: str) -> tuple[bool, dict | None, str]:
    """pi_record.cpp's parsePiRecord(): `(ok, record, id)`. Not ok = Drop: ACK it away if the id
    is known. ArduinoJson 7 rejects a bare NaN/Infinity token (which Python's json.dumps writes),
    but the firmware still recovers the top-level id from the raw line (REC-19). An unknown
    kind, an unknown class, or a reading without both probabilities (except No Face) is dropped."""
    try:
        record = json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        try:
            loose = json.loads(text)  # the raw-line id recovery
        except ValueError:
            return False, None, ""
        return False, None, loose.get("id", "") if isinstance(loose, dict) else ""
    if not isinstance(record, dict) or not record.get("id"):
        return False, None, ""
    rid = record["id"]
    kind = record.get("kind")
    if kind == "route_status":
        return True, record, rid
    if kind != "drowsiness":
        return False, None, rid
    payload = record.get("payload") or {}
    if payload.get("class_name") not in _CLASSES:
        return False, None, rid
    probabilities = payload.get("probabilities")
    if probabilities is None:
        if payload.get("class_name") != "No Face":
            return False, None, rid
    elif not (isinstance(probabilities, list) and len(probabilities) == 2):
        return False, None, rid
    return True, record, rid


def backend_severity(severity: str | None) -> str:
    """pi_event_policy.cpp's backendSeverity()."""
    return severity if severity in _ALERTING else "low"


def decide(record: dict, episodes: dict[str, str]) -> tuple[str, str | None]:
    """pi_event_policy.cpp's decidePiEvent(): `(action, alert id)` where action is nothing /
    alert / resolve / status, and the id is the episode's alert (escalation link or the one to
    resolve)."""
    if record["kind"] == "route_status":
        return "status", None
    p = record.get("payload") or {}
    severity = p.get("severity")
    if not p.get("transition") or severity not in _RANK:
        return "nothing", None
    previous = p.get("previous_severity")
    open_alert = episodes.get(p.get("episode") or "")
    if severity in _ALERTING and _RANK[severity] > _RANK.get(previous, 0):
        return "alert", open_alert
    if severity not in _ALERTING and previous in _ALERTING:
        return "resolve", open_alert
    return "nothing", None


def _iso_from_epoch_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PollResult:
    pulled: int
    acked: list[str]
    failed: list[tuple[str, str]]  # (id, reason)
    protocol_ok: bool
    dlq_waiting: int = 0
    replayed: int = 0


@dataclass
class SimulatedEsp32:
    listener: FakeListener
    http: AsyncClient
    device_key: str | None  # None = no credentials from the Pi yet
    route_id: str | None    # the cached active route (ensureActiveRoute); None = look it up
    clock: Callable[[], float]  # monotonic seconds, stands in for millis() / 1000
    gps: tuple[float, float, float] | None = (19.43, -99.13, 72.0)  # lat, lon, km/h
    wifi: bool = True  # backendIsReachable()
    # Faults a test injects. drop_next_ack: the link drops after the batch was handled, before
    # anything is reported. status_failures: that many route-status POSTs fail like a 5xx.
    drop_next_ack: bool = False
    status_failures: int = 0
    # Firmware state. episodes and panics are kept in NVS (they survive reboot()); the rest is
    # RAM only.
    episodes: dict[str, str] = field(default_factory=dict)
    panics: list[dict] = field(default_factory=list)
    live_severity: str | None = None
    # What reached the backend, stamped with this device's clock (for timing assertions).
    alert_posts: list[tuple[float, dict]] = field(default_factory=list)
    status_posts: list[tuple[float, dict]] = field(default_factory=list)
    _replay_stopped: bool = False

    def tick(self) -> None:
        """loop()'s work between Bluetooth polls: nothing the chain depends on (no grip yet)."""

    def reboot(self) -> "SimulatedEsp32":
        """A power loss: wiring, credentials, the episodes' alert ids and the panic queue come
        back from NVS; everything else (the live severity, the cached route) is gone."""
        return SimulatedEsp32(listener=self.listener, http=self.http,
                              device_key=self.device_key, route_id=None,
                              clock=self.clock, gps=self.gps, wifi=self.wifi,
                              episodes=dict(self.episodes), panics=list(self.panics))

    # --- esp32_argus.ino: loop() -------------------------------------------------------
    async def poll(self, *, half_open_before_ack: bool = False) -> PollResult:
        """One poll: the live outbox, then (backend reachable) a few dead letters.
        `half_open_before_ack` models an ESP32 reset right after END: nothing is reported and
        the link is never closed, so the Pi only notices when its own wait runs out."""
        live = await self._exchange(protocol.format_pull(PI_BT_PULL_COUNT), replay=False,
                                    half_open=half_open_before_ack)
        if self.panics:
            await self._flush_panics()
        if live.protocol_ok and live.dlq_waiting > 0 and await self._readiness() == "ready":
            self._replay_stopped = False
            replay = await self._exchange(protocol.format_dlq(DLQ_REPLAY_BATCH), replay=True)
            live.replayed = replay.pulled
            live.acked += replay.acked
            live.failed += replay.failed
        return live

    # --- bluetooth_client.cpp: bluetoothExchange() --------------------------------------
    async def _exchange(self, request: str, *, replay: bool, half_open: bool = False) -> PollResult:
        client, server_side = FakeTransport.pair()
        self.listener.offer(server_side)  # SerialBT.connect(piMac, PI_BT_CHANNEL)
        client.write((request + "\n").encode())

        acks: list[str] = []
        fails: list[tuple[str, str, str | None]] = []
        pulled, dlq_waiting, protocol_ok = 0, 0, True
        while True:
            line = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
            if line is None:
                protocol_ok = False
                break
            text = line.decode()
            end = protocol.parse_end(text)
            if end is not None:
                dlq_waiting = end
                break
            pulled += 1
            ok, record, rid = parse_record_line(text)
            if not ok:
                if rid:
                    acks.append(rid)  # unreadable: ACKed away so it can't block the queue
                continue
            kind, reason, body = await self._handle(record, replay)
            if kind == "ack":
                acks.append(rid)
            elif kind == "fail":
                fails.append((rid, reason, body))

        if half_open:
            return PollResult(pulled, [], [], False, dlq_waiting)
        if self.drop_next_ack and (acks or fails):
            self.drop_next_ack = False
            client.close()
            return PollResult(pulled, [], [], False, dlq_waiting)
        if protocol_ok and pulled:
            for rid, reason, body in fails:
                client.write((protocol.format_fail(rid, reason, body) + "\n").encode())
            if acks:
                client.write((format_ack(acks) + "\n").encode())
                acked = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
                if acked is None or not acked.decode().startswith("ACKED"):
                    protocol_ok = False
            if protocol_ok:
                client.write(b"DONE\n")
                client.readline(timeout=PI_BT_LINE_TIMEOUT_S)  # "OK acked=.. failed=.. dlq=.."
        client.close()  # SerialBT.disconnect()
        return PollResult(pulled, acks, [(r, why) for r, why, _ in fails], protocol_ok,
                          dlq_waiting)

    # --- esp32_argus.ino: handle() -------------------------------------------------------
    async def _handle(self, record: dict, replay: bool) -> tuple[str, str, dict | None]:
        if replay and self._replay_stopped:
            return "skip", "", None
        result = await self._handle_one(record, replay)
        if replay and result[0] == "fail":
            self._replay_stopped = True
        return result

    async def _handle_one(self, record: dict, replay: bool) -> tuple[str, str, dict | None]:
        p = record.get("payload") or {}
        if not replay and record["kind"] == "drowsiness" and p.get("severity") in _RANK:
            self.live_severity = p["severity"]
        action, episode_alert = decide(record, self.episodes)
        if action == "nothing":
            return "ack", "", None

        stored = (record.get("dlq") or {}).get("body")
        if stored:
            body = dict(stored)  # the original position and time
        elif action == "alert":
            body = {
                "alert_type": p.get("reason") or p.get("class_name"),
                "severity_level": backend_severity(p.get("severity")),
                "source": "fusion",
                "ai_metadata": {"scores": {}},
                "grip_status": "good",  # placeholder: no grip sensor yet
            }
            if p.get("model"):
                body["ai_metadata"]["model"] = p["model"]
            probabilities = p.get("probabilities")
            if probabilities:
                body["ai_metadata"]["scores"] = {"not_drowsy": probabilities[0],
                                                 "drowsy": probabilities[1]}
            if episode_alert:
                body["related_alert_id"] = episode_alert
            self._put_position(body, "coordinates", "speed_at_event")
            body["client_event_id"] = record["id"]
            self._put_time(body, record)
        elif action == "resolve":
            if not episode_alert:
                return "ack", "", None  # nothing this device knows to resolve
            when = self._occurred_at(record)
            if not when:
                return "fail", "no_time", None
            body = {"resolved_at": when}
        else:  # status
            if self.gps is None:
                return "ack", "", None  # no position to report: skipped
            body = {}
            self._put_position(body, "current_coordinates", "current_speed")
            body.update(odometer=0.0, vigilance=backend_severity(self.live_severity),
                        client_event_id=record["id"])
            self._put_time(body, record)

        ready = await self._readiness()
        if ready != "ready":
            return "fail", ready, body

        to_send = dict(body)
        if action == "resolve":
            path = f"/api/alerts/{episode_alert}/resolve"
        else:
            key = "coordinates" if action == "alert" else "current_coordinates"
            if action == "alert":
                to_send["id_route"] = self.route_id
                if episode_alert and "related_alert_id" not in to_send:
                    to_send["related_alert_id"] = episode_alert
            if key not in to_send:
                if self.gps is None:
                    return "fail", "no_gps", body
                self._put_position(to_send, key,
                                   "speed_at_event" if action == "alert" else "current_speed")
            path = "/api/alerts" if action == "alert" else f"/api/routes/{self.route_id}/status"

        if action == "status" and self.status_failures > 0:
            self.status_failures -= 1
            status = 503
            resp = None
        else:
            resp = await self.http.post(path, json=to_send, headers=self._headers())
            status = resp.status_code
        self._note_status(status, route_scoped=action != "resolve")

        if status in (200, 201):
            if action == "alert":
                self.alert_posts.append((self.clock(), to_send))
                if p.get("episode"):
                    self.episodes[p["episode"]] = resp.json()["id_alert"]
            elif action == "resolve":
                self.episodes.pop(p.get("episode") or "", None)
            else:
                self.status_posts.append((self.clock(), to_send))
            return "ack", "", None
        if status in (400, 409, 422):
            return "ack", "", None  # rejected for good: retrying can't succeed
        return "fail", f"http_{status}", body

    async def _readiness(self) -> str:
        """backend_api.cpp's backendReadiness()."""
        if not self.device_key:
            return "no_credentials"
        if not self.wifi:
            return "no_network"
        if self.route_id is None:
            self.route_id = await resolve_active_route(self.http, self.device_key)
            if self.route_id is None:
                return "no_route"
        return "ready"

    def _note_status(self, status: int, *, route_scoped: bool) -> None:
        """backend_api.cpp's backendNoteStatus(): a route-scoped 404/409 forgets the route."""
        if route_scoped and status in (404, 409):
            self.route_id = None

    def _put_position(self, body: dict, key: str, speed_key: str) -> None:
        if self.gps is not None:
            lat, lon, speed = self.gps
            body[key] = {"lat": lat, "lon": lon}
            body[speed_key] = speed

    def _occurred_at(self, record: dict) -> str:
        """The Pi's clock if NTP-synced, else GPS time (now), else nothing."""
        if record.get("clock_synced") and record.get("created_at_ms"):
            return _iso_from_epoch_ms(record["created_at_ms"])
        if self.gps is not None:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ""

    def _put_time(self, body: dict, record: dict) -> None:
        when = self._occurred_at(record)
        if when:
            body["occurred_at"] = when

    # --- esp32_argus.ino: handlePanicButton() -------------------------------------------
    async def press_panic(self) -> bool:
        """handlePanicButton(): the SMS (not modelled), then queued in NVS and flushed. True if
        it reached the backend right away; otherwise a later poll retries it."""
        body = {"alert_type": "panic_button", "severity_level": "critical",
                "source": "panic_button", "client_event_id": f"panic-{self.clock():.3f}"}
        self._put_position(body, "coordinates", "speed_at_event")
        self.panics.append(body)
        await self._flush_panics()
        return not self.panics

    async def _flush_panics(self) -> None:
        """flushPanics(): oldest first, while the backend takes them."""
        while self.panics:
            if await self._readiness() != "ready":
                return
            body = dict(self.panics[0])
            if "coordinates" not in body:
                if self.gps is None:
                    return
                self._put_position(body, "coordinates", "speed_at_event")
            body["id_route"] = self.route_id
            resp = await self.http.post("/api/alerts", json=body, headers=self._headers())
            if resp.status_code in (200, 201):
                self.alert_posts.append((self.clock(), body))
            elif resp.status_code not in (400, 409, 422):
                return
            self.panics.pop(0)

    def _headers(self) -> dict[str, str]:
        return {"X-Device-Api-Key": self.device_key or ""}
