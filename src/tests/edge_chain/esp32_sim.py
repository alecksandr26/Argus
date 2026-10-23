"""A Python stand-in for the ESP32 firmware (Bluetooth protocol v2 + the report sender), for the
edge-chain tests.

Mirrors `src/esp32-argus/src/esp32_argus/` module for module -- `parsePiRecord()` (pi_record.cpp),
`classifyPiEvent()` (pi_event_policy.cpp), `decidePulledRecord()` / the session report
(pull_session.cpp), `SenderPolicy` (sender_policy.cpp), `prepareReport()` / `finishReport()`
(sender_step.cpp), `reactToReport()` (ack_policy.cpp), the C2 body (backend_messages.cpp), the GPS
points and last known position (gps_track.cpp) and the loop/sender tasks (esp32_argus.ino) -- but
drives the *real* Python pieces on either side of it:

- the Pi side is cv-argus's real `SenderServer` + `Buffer`, reached over `FakeTransport` (the
  radio is the only simulated part);
- the cloud side is backend-argus's real FastAPI app: `POST /api/devices/me/report`, the only
  device write (docs/designs/final-tweaks-contracts.md C2), with a real per-truck device key.

What the firmware does, in short (contracts C1.8, C2.3): every `PULL` record updates the
vigilance; a non-event is ACKed; an event (C1.5) is TAKEN and held by the sender, which sends one
report per POST (the GPS points since the last report plus at most one event) by priority, retries
a live event inside its window (critical 60 s, medium/OK 30 s), then FAILs it to the Pi (a critical
with its composed event goes to the Pi's DLQ; a medium/OK is archived). A dead letter is replayed
`DLQ 1` at a time, one try each. Outcomes ride the next session until the Pi's `OK`.

Simplifications, on purpose: an attempt here is atomic (the firmware's pre-emption between AT
steps has nothing to stop), and GPS is injected as a plain value (a hardware reading). Keep this in
sync with the firmware by hand if either changes; there's no shared code, except the C1.5 vectors
(`test_firmware_vectors.py` replays `src/esp32-argus/test/vectors/event_vectors.json` here and the
host test replays them against pi_event_policy.cpp).
"""
from __future__ import annotations

import json
import time as real_time
from dataclasses import dataclass, field
from typing import Callable

from httpx import AsyncClient

from cv_argus.sender import (
    CONFIG_REQUEST,
    NOCONFIG_LINE,
    FakeListener,
    FakeTransport,
    parse_config,
    protocol,
)

# config.h (C4)
PI_BT_PULL_COUNT = 8
PI_BT_POLL_INTERVAL_S = 2.0
PI_BT_LINE_TIMEOUT_S = 3.0
REPORT_INTERVAL_S = 5.0
ACTIVE_ROUTE_RETRY_S = 30.0
MIN_SEND_GAP_S = 1.0
LIVE_RETRY_S = {"critical": 60.0, "medium": 30.0, "ok": 30.0}
REPORT_MAX_POINTS = 10
GPS_READ_INTERVAL_S = 1.0
PI_STALE_S = 10.0
SENDER_LIVE_SLOTS = 8
SENDER_OUTCOME_SLOTS = 16
DLQ_MAX_ATTEMPTS = 5
ID_MAX = 64  # C2.1: client_event_id / episode / alert_type are 1..64 chars

REPORT_PATH = "/api/devices/me/report"
BACKEND_REJECTION_REASONS = {"route_finished", "no_route", "too_old", "wrong_truck", "invalid"}

_RANK = {"none": 0, "low": 1, "medium": 2, "critical": 3}
_TIER = {"ok": 1, "medium": 2, "critical": 3}
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


# --- pi_record.cpp + pi_event_policy.cpp ---------------------------------------------------


def _reject_constant(token: str):
    raise ValueError(f"ArduinoJson rejects the token {token}")


def parse_record_line(text: str) -> tuple[bool, dict | None, str]:
    """pi_record.cpp's parsePiRecord(): `(ok, record, id)`. Not ok = Drop: ACK it away if the id
    is known. v2 has no route_status: any kind but "drowsiness" is dropped (C1.4). ArduinoJson 7
    rejects a bare NaN/Infinity token, but the firmware still recovers the top-level id from the
    raw line (REC-19). An unknown class, or a reading without both probabilities (except No Face),
    is dropped."""
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
    if record.get("kind") != "drowsiness":
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


def classify(transition: bool, severity: str | None, previous: str | None) -> str:
    """pi_event_policy.cpp's classifyPiEvent() (C1.5): "none" | "alert_medium" |
    "alert_critical" | "ok". A missing previous severity counts as none; an unknown severity is
    never an event."""
    if not transition or severity not in _RANK:
        return "none"
    sev = _RANK[severity]
    prev = _RANK.get(previous or "none", 0)
    if severity == "medium" and prev < 2:
        return "alert_medium"
    if severity == "critical" and prev < 3:
        return "alert_critical"
    if sev <= 1 and prev >= 2:
        return "ok"
    return "none"


def classify_record(record: dict) -> str:
    p = record.get("payload") or {}
    return classify(bool(p.get("transition")), p.get("severity"), p.get("previous_severity"))


def backend_vigilance(severity: str | None) -> str:
    """pi_event_policy.cpp's backendVigilance()."""
    if severity in ("none", "low"):
        return "low"
    if severity in ("medium", "critical"):
        return severity
    return "unknown"


# --- backend_messages.cpp ------------------------------------------------------------------


def round_age(seconds: float) -> float:
    return round(seconds, 1) if seconds > 0 else 0.0


def event_from_record(record: dict, kind: str, position: dict | None,
                      speed: float | None) -> dict:
    """backend_messages.cpp's eventFromRecord(): the C2.1 event minus age_s / delivery /
    queued_seconds."""
    p = record.get("payload") or {}
    probabilities = p.get("probabilities")
    event = {
        "client_event_id": record["id"],
        "episode": p.get("episode") or record["id"],
        "kind": "ok" if kind == "ok" else "alert",
        "severity": None,
        "alert_type": None,
        "position": position,
        "speed": speed,
    }
    if kind != "ok":
        event["severity"] = "critical" if kind == "alert_critical" else "medium"
        event["alert_type"] = p.get("reason") or p.get("class_name")
    model = p.get("model")
    scores = ({"not_drowsy": probabilities[0], "drowsy": probabilities[1]}
              if isinstance(probabilities, list) and len(probabilities) == 2 else None)
    event["ai_metadata"] = None if not model and scores is None else {"model": model or None,
                                                                       "scores": scores}
    return event


def record_fits_event(record: dict) -> bool:
    """backend_messages.cpp's recordFitsEvent()."""
    p = record.get("payload") or {}
    alert_type = p.get("reason") or p.get("class_name") or ""
    return (0 < len(record["id"]) <= ID_MAX and len(p.get("episode") or "") <= ID_MAX
            and len(alert_type) <= ID_MAX)


def event_from_stored(body: dict | None) -> dict | None:
    """backend_messages.cpp's eventFromStoredJson(): a dead letter's stored event, or None."""
    if not isinstance(body, dict):
        return None
    if not body.get("client_event_id") or not body.get("episode"):
        return None
    if body.get("kind") == "alert":
        if body.get("severity") not in ("medium", "critical") or not body.get("alert_type"):
            return None
    elif body.get("kind") != "ok":
        return None
    keep = ("client_event_id", "episode", "kind", "severity", "alert_type", "position", "speed",
            "ai_metadata")
    return {k: body.get(k) for k in keep}


def compose_event(event: dict, age_s: float, delivery: str, queued_s: float) -> dict:
    """backend_messages.cpp's composeEventJson()."""
    out = dict(event)
    out["age_s"] = round_age(age_s)
    out["delivery"] = delivery
    out["queued_seconds"] = round_age(queued_s) if delivery == "dlq" else 0
    return out


# --- ack_policy.cpp ------------------------------------------------------------------------


@dataclass
class Reaction:
    event: str = "no_event"  # no_event | delivered | rejected | failed
    reason: str = ""
    route_known: bool = False
    active_route: str | None = None
    recredential: bool = False


def react_to_report(status: int, body: dict | None, sent_event: bool,
                    transport_reason: str = "") -> Reaction:
    """ack_policy.cpp's reactToReport() (C2.3)."""
    fail = "failed" if sent_event else "no_event"
    if status <= 0:
        return Reaction(fail, transport_reason or "transport_error")
    if status == 401:
        return Reaction(fail, "http_401", recredential=True)
    if status in (413, 422):
        return Reaction("rejected" if sent_event else "no_event", "invalid")
    if not 200 <= status < 300:
        return Reaction(fail, f"http_{status}")
    if not isinstance(body, dict):
        return Reaction(fail, "bad_response")
    r = Reaction(route_known=True, active_route=body.get("active_route") or None)
    if not sent_event:
        return r
    event = body.get("event") or {}
    status_word = event.get("status")
    if status_word in ("created", "duplicate"):
        r.event = "delivered"
    elif status_word == "rejected":
        reason = event.get("reason")
        r.event, r.reason = "rejected", reason if reason in BACKEND_REJECTION_REASONS else "invalid"
    else:
        r.event, r.reason = "failed", "bad_response"
    return r


# --- sender_policy.cpp ---------------------------------------------------------------------


@dataclass
class Held:
    record_id: str
    event: dict
    tier: str  # critical | medium | ok
    dlq: bool
    base_age: float
    taken_at: float
    window: float = 0.0  # live retry window; 0 for a DLQ replay (one try)
    base_queued: float = 0.0
    prior_attempts: int = 0
    attempts: int = 0
    last_error: str = ""
    last_attempt_end: float = 0.0

    def age(self, now: float) -> float:
        return self.base_age + (now - self.taken_at)

    def queued(self, now: float) -> float:
        return self.base_queued + (now - self.taken_at) if self.dlq else 0.0


@dataclass
class Outcome:
    record_id: str
    ack: bool
    reason: str = ""
    body: dict | None = None
    seq: int = 0


class SenderPolicy:
    """sender_policy.cpp: cadence, priority, retry windows, the DLQ's one try, outcomes."""

    def __init__(self) -> None:
        self.live: list[Held] = []
        self.dlq: Held | None = None
        self.route = "unknown"  # unknown | none | active
        self.outcomes: list[Outcome] = []
        self.outcomes_pushed = 0
        self.outcomes_lost = 0
        self.ever_reported = False
        self.last_report_end = 0.0
        self.last_attempt_failed = False

    def holds(self, rid: str) -> bool:
        return any(h.record_id == rid for h in self.live) or (
            self.dlq is not None and self.dlq.record_id == rid)

    def outcome_pending_for(self, rid: str) -> bool:
        return any(o.record_id == rid for o in self.outcomes)

    def live_pending(self) -> bool:
        return bool(self.live)

    def wants_dlq(self) -> bool:
        return not self.live and self.dlq is None

    def take_live(self, rid: str, event: dict, tier: str, age_s: float, now: float) -> str:
        if self.holds(rid):
            return "already_held"
        if self.outcome_pending_for(rid):
            return "already_finished"
        if len(self.live) >= SENDER_LIVE_SLOTS or not 0 < len(rid) <= ID_MAX:
            return "full"
        self.live.append(Held(rid, event, tier, False, max(age_s, 0.0), now, LIVE_RETRY_S[tier]))
        if self.dlq is not None:  # not in flight in the sim: any live event comes first
            self._finish(self.dlq, False, "preempted")
            self.dlq = None
        return "queued"

    def take_dlq(self, rid: str, event: dict, age_s: float, queued_s: float, prior: int,
                 now: float) -> str:
        if self.holds(rid):
            return "already_held"
        if self.outcome_pending_for(rid):
            return "already_finished"
        if self.dlq is not None or not 0 < len(rid) <= ID_MAX:
            return "full"
        self.dlq = Held(rid, event, "critical", True, max(age_s, 0.0), now,
                        base_queued=max(queued_s, 0.0), prior_attempts=prior)
        return "queued"

    def expire(self, now: float) -> None:
        for h in list(self.live):
            if now - h.taken_at < h.window:
                continue
            self.live.remove(h)
            if h.tier == "critical":
                body = compose_event(h.event, h.age(now), "live", 0)
                self._finish(h, False, h.last_error or "retry_window", body)
            else:
                self._finish(h, False, "retry_window")

    def _best_live(self) -> Held | None:
        best = None
        for h in self.live:
            if best is None or _TIER[h.tier] > _TIER[best.tier] or (
                    h.tier == best.tier and h.taken_at < best.taken_at):
                best = h
        return best

    def plan(self, now: float, have_points: bool) -> tuple[str, Held | None, float]:
        """`(kind, held, wait_s)`: kind is live | dlq | points | wait."""
        best = self._best_live()
        if best is not None:
            due = best.last_attempt_end + MIN_SEND_GAP_S if best.attempts else now
            return ("live", best, 0.0) if now >= due else ("wait", None, due - now)
        if self.dlq is not None:
            due = self.last_report_end + MIN_SEND_GAP_S if self.last_attempt_failed else now
            return ("dlq", self.dlq, 0.0) if now >= due else ("wait", None, due - now)
        if not self.ever_reported:
            return "points", None, 0.0
        slow = not have_points or self.route == "none"
        due = self.last_report_end + (ACTIVE_ROUTE_RETRY_S if slow else REPORT_INTERVAL_S)
        return ("points", None, 0.0) if now >= due else ("wait", None, due - now)

    def end_attempt(self, held: Held | None, end: str, reason: str, now: float) -> None:
        """end: delivered | rejected | failed (an atomic attempt: never pre-empted here)."""
        self.ever_reported = True
        self.last_report_end = now
        self.last_attempt_failed = end == "failed"
        if held is None:
            return
        if not held.dlq:
            if end == "failed":
                held.attempts += 1
                held.last_error = reason or "transport_error"
                held.last_attempt_end = now
                return
            self.live.remove(held)
        else:
            self.dlq = None
        if end == "delivered":
            self._finish(held, True)
        else:
            self._finish(held, False, reason or ("invalid" if end == "rejected" else
                                                  "transport_error"))

    def _finish(self, held: Held, ack: bool, reason: str = "", body: dict | None = None) -> None:
        if len(self.outcomes) == SENDER_OUTCOME_SLOTS:
            self.outcomes.pop(0)
            self.outcomes_lost += 1
        self.outcomes.append(Outcome(held.record_id, ack, reason, body, self.outcomes_pushed))
        self.outcomes_pushed += 1

    def drop_outcomes_before(self, seq_end: int) -> None:
        self.outcomes = [o for o in self.outcomes if o.seq >= seq_end]


# --- the device --------------------------------------------------------------------------


@dataclass
class SessionResult:
    """One Bluetooth session, for assertions."""
    request: str
    pulled: int = 0
    acked: list[str] = field(default_factory=list)
    taken: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (id, reason), outcomes included
    left: int = 0
    protocol_ok: bool = False
    got_ok: bool = False
    dlq_waiting: int = 0
    outbox_waiting: int = 0
    summary: dict | None = None


@dataclass
class PollResult:
    live: SessionResult
    dlq: SessionResult | None = None

    @property
    def protocol_ok(self) -> bool:
        return self.live.protocol_ok


@dataclass
class Post:
    """One report attempt that reached the backend."""
    at: float          # the device's clock (the sim's millis() / 1000)
    wall: float        # real wall-clock seconds when it was posted
    body: dict
    status: int
    answer: dict | None


@dataclass
class SimulatedEsp32:
    listener: FakeListener
    http: AsyncClient
    device_key: str | None  # None = no credentials from the Pi yet
    clock: Callable[[], float]  # monotonic seconds, stands in for millis() / 1000
    gps: tuple[float, float, float] | None = (19.43, -99.13, 72.0)  # lat, lon, km/h
    gps_time: bool = True  # the NEO-6M has UTC time (ages a last known position from NVS)
    wifi: bool = True  # a transport to the backend
    # Faults a test injects. backend_failures: that many attempts answer 503. break_before_ok:
    # the next session that has report lines writes them all, then the link drops before DONE.
    backend_failures: int = 0
    break_before_ok: bool = False
    # NVS: survives reboot(). The last known position is (lat, lon); from this boot it carries
    # the fix's clock time, from NVS it needs GPS time to be aged.
    last_known: tuple[float, float] | None = None
    last_known_at: float | None = None
    last_known_from_nvs: bool = False
    # RAM.
    sender: SenderPolicy = field(default_factory=SenderPolicy)
    track: list[dict] = field(default_factory=list)
    pi_severity: str | None = None
    pi_seen_at: float | None = None
    next_gps_read: float | None = None
    posts: list[Post] = field(default_factory=list)
    sessions: list[SessionResult] = field(default_factory=list)

    # --- what reached the backend, for assertions ---
    @property
    def event_posts(self) -> list[Post]:
        return [p for p in self.posts if p.body.get("event")]

    @property
    def alert_posts(self) -> list[tuple[float, dict]]:
        """(device time, event) of every alert event the backend created."""
        return [(p.at, p.body["event"]) for p in self.posts
                if p.body.get("event") and p.body["event"]["kind"] == "alert"
                and (p.answer or {}).get("event", {}).get("status") == "created"]

    def reboot(self) -> "SimulatedEsp32":
        """A power loss: credentials and the last known position come back from NVS; the held
        events, outcomes, points and the known route are gone (the Pi's lease brings the rows
        back after 120 s)."""
        return SimulatedEsp32(
            listener=self.listener, http=self.http, device_key=self.device_key, clock=self.clock,
            gps=self.gps, gps_time=self.gps_time, wifi=self.wifi,
            last_known=self.last_known, last_known_at=None,
            last_known_from_nvs=self.last_known is not None,
        )

    # --- gpsTask ------------------------------------------------------------------------
    def _vigilance(self, now: float) -> str:
        if self.pi_seen_at is None or now - self.pi_seen_at >= PI_STALE_S:
            return "unknown"
        return backend_vigilance(self.pi_severity)

    def _last_known_age(self, now: float) -> float | None:
        if self.last_known is None:
            return None
        if self.last_known_at is not None:
            return now - self.last_known_at
        return 0.0 if self.gps_time else None  # from NVS: only with GPS time (C1.8 [sync 2])

    def _read_gps(self, now: float) -> None:
        """gps_track.cpp's trackPointFor() + LastKnownPosition.onRead()."""
        vigilance = self._vigilance(now)
        if self.gps is not None:
            lat, lon, speed = self.gps
            self.last_known, self.last_known_at, self.last_known_from_nvs = (lat, lon), now, False
            point = {"at": now, "lat": lat, "lon": lon, "speed": speed, "vigilance": vigilance,
                     "last_known": False, "location_age_s": 0}
        else:
            age = self._last_known_age(now)
            if age is None:
                return
            lat, lon = self.last_known
            point = {"at": now, "lat": lat, "lon": lon, "speed": 0.0, "vigilance": vigilance,
                     "last_known": True, "location_age_s": round_age(age)}
        self.track.append(point)
        del self.track[:-REPORT_MAX_POINTS]

    def _here(self, now: float) -> tuple[dict | None, float | None]:
        """gps_track.cpp's eventPosition() and the speed of a current fix."""
        if self.gps is not None:
            lat, lon, speed = self.gps
            return {"lat": lat, "lon": lon, "last_known": False, "location_age_s": 0}, speed
        age = self._last_known_age(now)
        if age is None:
            return None, None
        lat, lon = self.last_known
        return {"lat": lat, "lon": lon, "last_known": True, "location_age_s": round_age(age)}, None

    async def tick(self) -> None:
        """What runs between Bluetooth polls: gpsTask's 1 s reads, then the sender task until
        nothing is due."""
        now = self.clock()
        if self.next_gps_read is None:
            self.next_gps_read = now
        while now >= self.next_gps_read:
            self._read_gps(now)
            self.next_gps_read += GPS_READ_INTERVAL_S
        await self.send_due()

    # --- senderTask ---------------------------------------------------------------------
    async def send_due(self, max_attempts: int = 20) -> int:
        """prepareReport() / backendPostReport() / finishReport() while something is due now.
        Returns how many attempts were made."""
        made = 0
        while made < max_attempts:
            now = self.clock()
            self.sender.expire(now)
            kind, held, _ = self.sender.plan(now, bool(self.track))
            if kind == "wait":
                return made
            made += 1
            points, self.track = self.track[-REPORT_MAX_POINTS:], []
            body = {"points": [
                {"age_s": round_age(now - p["at"]), "lat": p["lat"], "lon": p["lon"],
                 "speed": 0.0 if p["last_known"] else p["speed"], "vigilance": p["vigilance"],
                 "last_known": p["last_known"], "location_age_s": p["location_age_s"]}
                for p in points]}
            if held is not None:
                body["event"] = compose_event(held.event, held.age(now),
                                              "dlq" if held.dlq else "live", held.queued(now))
            status, answer, reason = await self._post(body)
            r = react_to_report(status, answer, held is not None, reason)
            if r.route_known:
                self.sender.route = "active" if r.active_route else "none"
            if r.recredential:
                self.sender.route = "unknown"
            if held is None:
                self.sender.end_attempt(None, "delivered" if r.route_known else "failed",
                                        r.reason, now)
            elif r.event == "delivered":
                self.sender.end_attempt(held, "delivered", "", now)
            elif r.event == "rejected":
                self.sender.end_attempt(held, "rejected", r.reason, now)
            else:
                self.sender.end_attempt(held, "failed", r.reason or "bad_response", now)
        return made

    async def _post(self, body: dict) -> tuple[int, dict | None, str]:
        """backend_api.cpp's backendPostReport()."""
        if not self.device_key:
            return -1, None, "no_credentials"
        if not self.wifi:
            return -1, None, "no_network"
        if self.backend_failures > 0:
            self.backend_failures -= 1
            return 503, None, ""
        resp = await self.http.post(REPORT_PATH, json=body,
                                    headers={"X-Device-Api-Key": self.device_key})
        try:
            answer = resp.json()
        except ValueError:
            answer = None
        self.posts.append(Post(self.clock(), real_time.time(), body, resp.status_code,
                               answer if isinstance(answer, dict) else None))
        return resp.status_code, answer if isinstance(answer, dict) else None, ""

    # --- the loop task: Bluetooth sessions ------------------------------------------------
    async def poll(self, *, half_open_before_report: bool = False,
                   lose_report: bool = False) -> PollResult:
        """loop(): a `PULL` session, then `DLQ 1` when the Pi holds dead letters, no live event
        is pending and there are credentials (C1.8). Then the sender runs (it is its own task on
        the device, so it never waits for the next poll). `half_open_before_report` models an
        ESP32 reset right after END: nothing is decided or reported and the link is never closed.
        `lose_report`: the records are decided (events held by the sender) but the link drops
        before any report line reaches the Pi."""
        live = self._session(protocol.format_pull(PI_BT_PULL_COUNT), dlq=False,
                             half_open=half_open_before_report, lose_report=lose_report)
        result = PollResult(live)
        if (live.got_ok and live.dlq_waiting > 0 and self.sender.wants_dlq()
                and self.device_key):
            result.dlq = self._session(protocol.format_dlq(1), dlq=True)
        await self.send_due()
        return result

    def _decide(self, ok: bool, record: dict | None, rid: str, dlq: bool, now: float) -> str:
        """pull_session.cpp's decidePulledRecord(): ack | take | fail_too_old | fail_invalid |
        leave."""
        if not ok:
            return "ack" if rid else "leave"
        p = record.get("payload") or {}
        if not dlq:
            self.pi_severity, self.pi_seen_at = p.get("severity"), now
        kind = classify_record(record)
        if kind == "none":
            return "ack"
        if record.get("age_s") is None:
            return "fail_too_old"
        if not record_fits_event(record):
            return "fail_invalid"
        tier = {"alert_critical": "critical", "alert_medium": "medium", "ok": "ok"}[kind]
        if not dlq:
            position, speed = self._here(now)
            took = self.sender.take_live(rid, event_from_record(record, kind, position, speed),
                                         tier, record["age_s"], now)
        else:
            dead = record.get("dlq") or {}
            event = event_from_stored(dead.get("body"))
            if event is None:  # esp32_unreachable: composed now, never with today's position
                event = event_from_record(record, kind, None, None)
            event["client_event_id"] = rid
            took = self.sender.take_dlq(rid, event, record["age_s"], dead.get("queued_s") or 0,
                                        dead.get("attempts") or 0, now)
        return "take" if took in ("queued", "already_held") else "leave"

    def _session(self, request: str, *, dlq: bool, half_open: bool = False,
                 lose_report: bool = False) -> SessionResult:
        """bluetoothOpenSession() + the decisions + bluetoothCloseSession()."""
        result = SessionResult(request)
        self.sessions.append(result)
        client, server_side = FakeTransport.pair()
        self.listener.offer(server_side)  # SerialBT.connect(piMac, PI_BT_CHANNEL)
        client.write((request + "\n").encode())
        lines: list[str] = []
        while True:
            line = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
            if line is None:
                client.close()
                return result
            text = line.decode()
            end = protocol.parse_end(text)
            if end is not None:
                result.dlq_waiting, result.outbox_waiting = end
                break
            lines.append(text)
        result.protocol_ok = True
        result.pulled = len(lines)
        if half_open:
            return result  # never reported, never closed

        now = self.clock()
        fails: list[tuple[str, str, dict | None]] = []
        for text in lines:
            ok, record, rid = parse_record_line(text)
            action = self._decide(ok, record, rid, dlq, now)
            if action == "ack":
                result.acked.append(rid)
            elif action == "take":
                result.taken.append(rid)
            elif action == "fail_too_old":
                fails.append((rid, "too_old", None))
            elif action == "fail_invalid":
                fails.append((rid, "invalid", None))
            else:
                result.left += 1
        seq_end = self.sender.outcomes_pushed
        for o in self.sender.outcomes:
            if o.ack:
                result.acked.append(o.record_id)
            else:
                fails.append((o.record_id, o.reason, o.body))
        result.failed = [(rid, why) for rid, why, _ in fails]
        if lose_report:
            client.close()
            return result

        # pi_protocol.cpp's buildReportScript(): FAILs, ACK (chunked), TAKEN (chunked), DONE.
        script: list[tuple[str, str]] = [(protocol.format_fail(r, why, body), "")
                                         for r, why, body in fails]
        for i in range(0, len(result.acked), 12):
            script.append((protocol.format_ack(result.acked[i:i + 12]), "ACKED"))
        for i in range(0, len(result.taken), 12):
            script.append((protocol.format_taken(result.taken[i:i + 12]), "LEASED"))
        breaking = self.break_before_ok and len(script) > 0
        if breaking:
            self.break_before_ok = False
        else:
            script.append(("DONE", "OK"))
        for line, expect in script:
            client.write((line + "\n").encode())
            if not expect:
                continue
            reply = client.readline(timeout=PI_BT_LINE_TIMEOUT_S)
            if reply is None:
                break
            text = reply.decode()
            if expect == "OK":
                result.summary = protocol.parse_result(text)
                result.got_ok = result.summary is not None
                break
            if not text.startswith(expect):
                break
        client.close()  # SerialBT.disconnect()
        if result.got_ok:
            self.sender.drop_outcomes_before(seq_end)
        return result
