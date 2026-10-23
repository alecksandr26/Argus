# Argus final tweaks: frozen interface contracts (C1–C4)

Status: **frozen** for rounds 1–3. Companion to `docs/designs/final-tweaks-plan.md`, which
explains *why*; this file says *exactly what*. Where this file and the plan differ in detail, this
file wins. Where this file and older docs (module `CLAUDE.md`s, READMEs, `protocol.py`
docstrings) differ, this file wins.

Changes only through the lead, at a sync, as one commit on `feat/final-tweaks`. Teams rebase
onto it. If something here is ambiguous or wrong, **don't guess across a team boundary**: code
the most conservative reading, and put the question in your report's `contract_questions`.

**Sync 1 amendments** (answers to round-1 contract questions; marked `[sync 1]` below):
C1.7 face-switch reading and frame ordering; C1.8 ESP32 dedupes held rows by id (TAKEN still
same-session only); C2.2 `kind: ok` is routed by time like an alert; C2.3 cadence before the first
200; C2.4 409 on both device-resolve paths, legacy `low` creates get no incident; C3.1
`RouteCreate.actual_departure` + `DELETE /api/routes/{id}` cascade; C3.5 points never raise
`incident_state`; C3.6 UI trusts the server `tier` and drops track of routes that left the active
set.

**Sync 2 amendments** (after build round 1; marked `[sync 2]` below): section 0 three ownership
additions; C1.2 report lines take effect when read; C1.6 preempted outbox row stays put, a repeated
FAIL is ignored, `cap_unsent` spares leased rows, `serve_dlq`/`fetch_dlq` split; C1.7 face-lock
matching, re-acquisition and take-over; C1.8 confirmed ESP32 readings; C2.2 rule 4 says
`route_finished` for any event after the truck's last route ended, route status re-checked under
the lock; C2.3 empty-points liveness report; C2.4 user `resolved_at` is a 422; C3.1 a finished
route can't be reopened; C3.2 `clip_seconds`; C3.4 severity floor; C3.6 `since` overlap and the
route view's path; C4 new constants.

**Sync 3 amendments** (after build round 2; marked `[sync 3]` below): section 0 ownership
(notebook 14 whole to T2, `src/tests/smoke_cv_argus.py` + `src/tests/requirements.txt` to T3, the
root compose `simulator-argus` service to T6); C1.5 an unknown/missing `previous_severity` ranks
as `none`; C1.6 an ignored repeated FAIL still fills a NULL `dlq_body`; C1.7 bystander grace,
switch-timer reset and take-over gaps as built, `face_lock` log fields; C2.1 point timestamps keep
millisecond precision (no rounding) and the ESP32 measures `age_s` at send time; C2.3 one lock
deadline per request; C3.4 `closed_by` is `null` for migration closures; C3.6 stop-route 422 text
pinned, 503 on stop/complete; C3.7 UI test hooks (close confirmation, tier regions); C4 notebook-only
constants note.

Conventions used throughout:
- Times on the wire between devices are **relative** (`age_s`, seconds, float, >= 0). Only the
  backend turns them into absolute times, with its own clock.
- Absolute times in backend JSON are ISO-8601 UTC strings with a `Z` or `+00:00` suffix
  (the existing `UtcDatetime` serializer).
- Coordinates are always `{lat, lon}` in decimal degrees at every API boundary (GeoJSON stays
  internal to the backend, unchanged).
- Speeds are **km/h**. Distances are metres. Durations without a unit suffix are seconds.
- Severity words: the Pi uses `none | low | medium | critical`; the backend `Severity` enum is
  `low | medium | critical` (Pi `none` and `low` both map to backend `low`).

---

## 0. Ownership and merge order

| Team | Branch / worktree | Owns (edit only these) |
|---|---|---|
| T1 Pi messaging | `feat/final-tweaks-t1-pi-messaging` / `.claude/worktrees/ft-t1-pi-messaging` | `src/cv-argus/src/orchestrator/`, `src/cv-argus/src/buffer/`, `src/cv-argus/src/sender/`, `src/cv-argus/src/alerts/`; in `src/cv-argus/src/constants.py` only the `RULE_*`, `ORCHESTRATOR_*`, `BUFFER_*` names; `src/cv-argus/tests/test_constants.py` and the tests of the packages above; `src/cv-argus/CLAUDE.md` + `README.md` (T2 sends its text through the lead) |
| T2 Vision | `feat/final-tweaks-t2-vision` / `.claude/worktrees/ft-t2-vision` | `src/cv-argus/src/pipeline/face_detector_stage.py`, `src/cv-argus/src/pipeline/parts_stages.py`, `src/cv-argus/src/model/`; in `constants.py` only the `PARTS_*`, `FACE_*` names; their tests; `src/notebook/12_tiny_grid_detector.ipynb`; `src/dataset/` if needed |
| T3 ESP32 | `feat/final-tweaks-t3-esp32` / `.claude/worktrees/ft-t3-esp32` | `src/esp32-argus/**`, `src/tests/edge_chain/`, `src/tests/smoke_backend.py` |
| T4 Backend | `feat/final-tweaks-t4-backend` / `.claude/worktrees/ft-t4-backend` | `src/backend-argus/**`, `src/infra-argus/**` |
| T5 UI | `feat/final-tweaks-t5-ui` / `.claude/worktrees/ft-t5-ui` | `src/ui-argus/**` |
| T6 Sim + IT | `feat/final-tweaks-t6-sim-it` / `.claude/worktrees/ft-t6-sim-it` | `src/simulator-argus/**`, `src/it-argus/**` |
| Lead | `feat/final-tweaks` / `.claude/worktrees/final-tweaks-pi-esp32` | this file, the plan, root `CLAUDE.md`, `docs/roadmap.md`, root `docker-compose.yml` |

`[sync 2]` Ownership additions (paths nobody owned):
- **T1**: `src/cv-argus/src/main.py` and `src/cv-argus/docker-compose.yml`, **comments and
  docstrings only** (remove the heartbeat mentions).
- **T2**: `src/cv-argus/src/pipeline/output_stages.py` (only to add the `face_lock` fields to the
  per-frame log line and the metrics JSONL, plus their tests), and
  `src/notebook/14_backbone_grid_detector.ipynb` **cell 26 only** (keep `grid_postprocess`
  verbatim with notebook 12's, eye filter included, and say so in its markdown).
- **T3**: `src/tests/README.md` (the `smoke_backend.py` / `edge_chain` sections).

`[sync 3]` Ownership changes:
- **T2**: the **whole** `src/notebook/14_backbone_grid_detector.ipynb` (replaces "cell 26 only";
  its grid-head copy is cells 11 markdown + 12 code). Use: mirror notebook 12's tf.data,
  augmentation, eval-view, BN/int8 calibration and scoring changes verbatim where the cells are
  shared copies, and label its comparison table. No other change of scope.
- **T3**: `src/tests/smoke_cv_argus.py` (port it to protocol v2) and `src/tests/requirements.txt`
  (add `gdown`, so `edge_chain` no longer needs its stub).
- **T6**: the `simulator-argus` service block of the **root** `docker-compose.yml` (its
  `environment:` names and defaults only), so it lands with T6's PR. The rest of that file stays
  the lead's.

`constants.py` is shared by T1 and T2 **by name prefix**: T1 adds/edits/removes only
`RULE_*`, `ORCHESTRATOR_*`, `BUFFER_*`; T2 only `PARTS_*`, `FACE_*`. Put new constants in the
section of the file that already holds that prefix, to keep the two diffs apart.

T1 ↔ T2 code seam: T2 calls `PartsRuleEngine.reset_signals()` (T1 writes it, signature in C1.7).
If T2 needs it before T1 lands, T2 calls it through `getattr(engine, "reset_signals", None)` and
tests with a fake engine; don't edit `parts_rules.py`.

**Merge order** (draft PRs into `feat/final-tweaks`, the user merges):
1. **T4 + T6 together** (backend gains the report endpoint while legacy endpoints still work).
2. **T1 + T2 + T3 together** (Bluetooth v2 is not compatible with v1; Pi image and ESP32
   firmware deploy together).
3. **T5** (UI on the new backend shapes).

---

## C1. Pi ↔ ESP32 Bluetooth protocol v2

Transport is unchanged (RFCOMM, `BLUETOOTH_CHANNEL` 4, the ESP32 is the client and opens a fresh
connection per session, one session at a time, newline-terminated UTF-8 lines, a trailing `\r`
tolerated, case-sensitive). **v2 is not compatible with v1**; there is no version negotiation.

### C1.1 Session kinds

The first line the ESP32 sends decides the session kind:

| First client line | Session |
|---|---|
| `CONFIG` | config session (unchanged from v1) |
| `PULL <n>` | live pull |
| `DLQ <k>` | dead-letter pull |
| `ACK <ids>` or `FAIL <id> <reason> <body>` | report session |
| anything else | Pi logs a warning and closes the connection |

The Pi waits up to **5 s** for the first line (unchanged).

### C1.2 Grammar

```
# Config session (unchanged)
C: CONFIG
S: CONFIG {"truck_id":..,"device_api_key":..,"backend_url":.., optional wifi_*/gprs_* keys}
   | NOCONFIG

# Live pull
C: PULL <n>                       n clamped to MAX_PULL_LIMIT (50); the ESP32 asks PI_BT_PULL_COUNT (8)
S: <record JSON line>  x0..n      outbox rows, not leased, oldest first (rowid ASC)
S: END <dlq_waiting> <outbox_waiting>
C: <report line>  x0..m           ACK / FAIL / TAKEN, any order (C1.3)
C: DONE
S: OK acked=<a> failed=<f> leased=<l> dlq=<d> outbox=<o>

# Dead-letter pull
C: DLQ <k>                        k clamped to MAX_DLQ_LIMIT (1): at most ONE row per session
S: <record JSON line with "dlq">  x0..1
S: END <dlq_waiting> <outbox_waiting>
C: <report line>  x0..m
C: DONE
S: OK acked=.. failed=.. leased=.. dlq=.. outbox=..

# Report session (results for rows taken in an earlier session)
C: ACK <ids> | FAIL <id> <reason> <body>     first line
C: <report line>  x0..m
C: DONE
S: OK acked=.. failed=.. leased=.. dlq=.. outbox=..
```

- `END` is always sent in a pull session, even with 0 rows. If 0 rows were sent, the client may
  still send report lines before `DONE` (e.g. a pending ACK), or just `DONE`, or disconnect.
- `<dlq_waiting>` = rows in state `dlq` and **not leased**; `<outbox_waiting>` = rows in state
  `outbox` and **not leased**, both counted *after* this session's rows were served (rows just
  served are not leased yet, so they are still counted).
- The Pi waits for report lines up to `ack_timeout` = **30 s** total (unchanged), polling 1 s at a
  time; a disconnect or timeout ends the session and leaves every unreported row exactly where it
  was (not leased, not sent).
- `OK` is the Pi's last line; then it closes. `failed` counts FAIL lines applied in this session,
  `leased` rows leased in this session; `dlq`/`outbox` as in `END`, counted after the session.
- `[sync 2]` Report lines **take effect the moment the Pi reads them** (`ACKED`/`LEASED` are sent
  right away). `DONE` -> `OK` is only a summary: a session that breaks before `OK` keeps every line
  already applied. The ESP32 may resend outcomes it has no `OK` for (C1.8); that is safe because
  ACK is idempotent and a repeated FAIL is ignored (C1.6).

### C1.3 Report lines

| Line | Pi reply | Meaning |
|---|---|---|
| `ACK <id1>,<id2>,...` | `ACKED <n>` (`n` = rows actually marked sent) | delivered, or nothing to deliver (keepalive, non-event, unusable row). Row → `sent`, lease cleared. |
| `FAIL <id> <reason> <body>` | *(no reply)* | couldn't deliver; `<reason>` is one token `[A-Za-z0-9_.:-]+`; `<body>` is compact JSON (an object) or `-`. Applied per C1.6. |
| `TAKEN <id1>,<id2>,...` | `LEASED <n>` (`n` = rows actually leased) | the ESP32 holds these rows for its sender task and will report each later with ACK or FAIL. |

- ids are comma-separated, no spaces; the regexes stay as in v1 (`_ACK_RE`, `_FAIL_RE`); `TAKEN`
  uses the same shape as `ACK`.
- `TAKEN` is only honoured for ids **served in this same session**; other ids are ignored (not
  counted in `LEASED`).
- `ACK`/`FAIL` are honoured for any id whose row is in `outbox` or `dlq` (leased or not). An ACK
  for an already-`sent` or archived id is ignored (not counted). A FAIL for an unknown,
  `sent` or archived id is ignored.
- The ESP32 must `TAKEN` every row it did not finish (ACK/FAIL) inside the session. A row served
  but neither ACKed, FAILed nor TAKEN stays where it was and will be served again.

### C1.4 Record JSON (one line per row, `alerts/serialization.to_json` + serve-time fields)

```json
{
  "id": "3f2a…(32 hex, uuid4)",
  "kind": "drowsiness",
  "level": 2,
  "created_at_ms": 1792764000123,
  "source_id": "pi_camera",
  "payload": {
    "class_name": "Drowsy",
    "probabilities": [0.05, 0.95],
    "transition": true,
    "severity": "critical",
    "previous_severity": "medium",
    "episode": "a1b2c3d4e5f6",
    "reason": "eyes_closed",
    "model": "parts-rules-v1",
    "trigger_s": 3.2,
    "timers": {"eyes_closed": 3.2, "head_down": 0.0, "mouth_open": 0.0, "no_face": 0.0},
    "yawn_events": 0
  },
  "geolocation": null,
  "mono_ms": 81234567,
  "boot_id": "…",
  "clock_synced": true,
  "age_s": 1.4,
  "dlq": {"error": "no_network", "body": {…}|null, "failed_at_ms": 1792764060000,
          "attempts": 2, "queued_s": 37}
}
```

Field rules:
- `kind` is **always `"drowsiness"`** in v2. `route_status` is gone (no heartbeat, no
  `route_status()` factory, `AlertKind.ROUTE_STATUS` removed). The ESP32 ACKs (drops) any row of
  another kind.
- `level`: 2 = `Drowsy`, 1 = `Not Drowsy`, `null` = `No Face` (unchanged).
- `payload.class_name`: `"Drowsy" | "Not Drowsy" | "No Face"` (unchanged).
- `payload.probabilities`: `[p_not_drowsy, p_drowsy]` floats, or `null` (only allowed when
  `class_name == "No Face"`; unchanged).
- `payload.transition`: `true` on a class change or a severity change, `false` on a keepalive.
- `payload.severity`: `"none" | "low" | "medium" | "critical"`. Always present in v2 records.
- `payload.previous_severity`: the severity of the previous record of this source (`=
  severity` on a keepalive). May be `null`/absent on the very first record: treat as `"none"`.
- `payload.episode`: 12 lowercase hex chars. Present on every record from the moment the
  severity reaches `medium` until **and including** the record that drops it back to
  `none`/`low` (the recovery). Absent otherwise.
- `payload.reason`: one of `ok`, `yawning`, `eyes_closed`, `yawn_eyes_closed`, `head_down`,
  `head_down_eyes_closed`, `no_face` (the rule engine's held reason). Unknown values must be
  passed through as-is, not rejected.
- `payload.model`, `trigger_s`, `timers`, `yawn_events`: informational, may be absent.
- `age_s` (**new, computed by the Pi at serve time, not stored**): seconds from the event to the
  moment the line is written, float >= 0, rounded to 0.1 s.
  - if the row's `boot_id` equals the Pi's current boot id and `mono_ms` is not null:
    `(monotonic_ms_now - mono_ms) / 1000`;
  - else if the row's `clock_synced` is true **and** the Pi's clock is synced now:
    `(time_ms_now - created_at_ms) / 1000` (clamped to >= 0);
  - else `null` (age unknowable). The ESP32 then sends `FAIL <id> too_old -` (archived).
- `dlq` is present **only** on rows served by `DLQ <k>`:
  `error` (last failure reason), `body` (the stored event JSON, see C1.6, or `null`),
  `failed_at_ms` (wall-clock ms when it first entered the DLQ), `attempts` (int, failed attempts
  so far), `queued_s` (**new**, int seconds since it first entered the DLQ, from wall clock,
  clamped >= 0).

### C1.5 Which records are backend events (both sides use this exact rule)

Let `rank(none)=0, rank(low)=1, rank(medium)=2, rank(critical)=3`, `prev = previous_severity or
"none"`, `sev = severity`. Only rows with `transition == true` can be events:

| Event | Condition | Backend event |
|---|---|---|
| **alert medium** | `sev == "medium"` and `rank(prev) < 2` | `kind: "alert"`, `severity: "medium"` |
| **alert critical** (raise or escalation) | `sev == "critical"` and `rank(prev) < 3` | `kind: "alert"`, `severity: "critical"` |
| **driver OK** | `rank(sev) <= 1` and `rank(prev) >= 2` | `kind: "ok"` |
| *(no event)* | everything else: keepalives, `none<->low`, `critical->medium`, class-only changes | ESP32 ACKs it after using it for `vigilance` |

`[sync 3]` An **unknown or missing** `previous_severity` (`null`, absent, `""`, or any value outside
the four words) ranks as `none`: e.g. a medium after `"bogus"` is an alert medium. The Pi never
emits such a value; the rule only keeps the Pi (Python and SQL) and the ESP32 from drifting on
hand-made or corrupted rows. The shared vectors in `src/esp32-argus/test/vectors/event_vectors.json`
(shape `{cases:[{name,transition,severity,previous_severity,expect}]}`, `expect` in
`alert_medium|alert_critical|ok|none`) are the executable form of this table: T3 keeps the path
and shape stable, T1's suite replays them.

A **critical event row** (the only kind the DLQ holds) is a row with `kind='drowsiness'`,
`payload.transition = true`, `payload.severity = 'critical'`, `COALESCE(payload.previous_severity,
'none') != 'critical'`.

Pi-side event emission (T1, orchestrator + rules):
- No face: the ladder is gone. `no_face` fires **only** `critical`, after
  `RULE_NO_FACE_CRITICAL_S = 5.0` s without a face (after a face was seen). No low/medium step.
- One alert per incident: the orchestrator already emits a transition on every severity change;
  a reason change at the same severity emits nothing (unchanged). Medium -> critical is one
  escalation record; `critical -> medium` produces a record but it is not an event (C1.5).
- Recovery: when the held severity falls to `none`/`low` (after `RULE_RECOVERY_S` = 2 s of a clear
  face, unchanged) the recovery record is emitted **immediately**, not debounced behind the class
  debounce, and carries the episode id.
- Keepalives every `ORCHESTRATOR_KEEPALIVE_SECONDS` = 2 s (unchanged). Keepalives never reach the
  backend; the ESP32 uses them only for `points[].vigilance`.
- The 60 s heartbeat is **removed** (`ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS` deleted).

### C1.6 Pi buffer states, lease, FAIL handling, archive (T1)

Row states in table `alerts`: `outbox`, `dlq`, `sent` (unchanged names). New table `archive`.
New columns on `alerts`: `lease_until_ms INTEGER` (wall-clock epoch ms, NULL = not leased),
`dlq_round INTEGER NOT NULL DEFAULT 0`, `last_error TEXT` (or reuse `dlq_error`). `attempts`
exists already.

**Lease.** `TAKEN` sets `lease_until_ms = now_ms + BUFFER_LEASE_SECONDS*1000` (120 s) on rows in
`outbox` or `dlq`. A row is *leased* while `lease_until_ms > now_ms`. Leased rows are skipped by
`fetch_unsent`, `fetch_dlq`, `expire_outbox` and by the `END`/`OK` waiting counts. When a lease
expires the row simply becomes visible again **in the state it had** (an `outbox` row returns to
the outbox, a `dlq` row to the DLQ); an expired lease is **not** an attempt. ACK and FAIL clear
the lease.

**Live outbox expiry** (`expire_outbox`, every housekeeping pass, cutoff
`DEFAULT_OUTBOX_MAX_AGE_SECONDS` = 60 s by `enqueued_at_ms`, non-leased rows only):
- critical event row -> `dlq`, `dlq_error = 'esp32_unreachable'`, `attempts` unchanged (0: no
  attempt was made), `dlq_round` per "DLQ order" below;
- any other event row (medium alert, OK) -> `archive`, reason `esp32_unreachable`;
- non-event rows (keepalives etc.) -> deleted.

**FAIL `<id> <reason> <body>`** (for a row in `outbox` or `dlq`):
1. Row is **not** a critical event row -> moved to `archive` at once with
   `archive_reason = <reason>` (the DLQ holds criticals only; the ESP32 sends FAIL for a medium/OK
   it gave up on, normally reason `retry_window`).
2. Critical event row and `<reason>` is `preempted` -> back to `dlq` (state `dlq` if it was),
   lease cleared, **`attempts` unchanged**, `dlq_round` unchanged. (Used only for a DLQ replay
   that a live event pre-empted.)
3. Critical event row and `<reason>` is in **`BUFFER_ARCHIVE_NOW_REASONS`** =
   `{route_finished, no_route, too_old, wrong_truck, invalid}` -> `archive` at once.
4. Any other reason -> `attempts += 1`; if the row was in `outbox` it moves to `dlq`
   (`dlq_at_ms` set, `dlq_round` per "DLQ order"); `dlq_error = <reason>`; if `<body>` is a JSON
   object it **replaces** `dlq_body` only when `dlq_body` is NULL (the first composed message,
   with the event-time position, is kept). Then if `attempts >= BUFFER_DLQ_MAX_ATTEMPTS` (5) ->
   `archive`, `archive_reason = 'max_attempts'`, `last_error = <reason>`.

The ESP32's live failure that dead-letters a critical is therefore attempt 1; each DLQ replay is
one attempt; 5 failures archive.

`[sync 2]` FAIL clarifications:
- Rule 2 on a critical still in `outbox` (a live row): it **stays in `outbox`**, lease cleared,
  `attempts`/`dlq_round` unchanged (it will be served live again). Only DLQ replays are expected
  to be pre-empted (C1.8), but this is the defined behaviour for both states.
- **Repeated FAIL guard (idempotency).** Rule 4 applies only to a row in `outbox` (leased or not)
  or to a **leased** `dlq` row. A rule-4 FAIL for a **non-leased** `dlq` row is ignored: nothing
  changes and it is not counted in `failed`. Every FAIL clears the lease, so a FAIL line resent
  after a broken session (C1.2) can never count an attempt twice. Rules 1-3 need no guard (an
  archived row ignores everything; rule 2 changes nothing countable). Consequence, accepted: a
  DLQ replay whose 120 s lease lapsed before its FAIL arrived is an uncounted attempt.
- `[sync 3]` An ignored rule-4 FAIL still **fills `dlq_body` when it is NULL** and `<body>` is a
  JSON object (`COALESCE(dlq_body, <body>)`); nothing else changes (no attempt, no `dlq_error`,
  not counted in `failed`). Case: a live critical TAKEN by the ESP32 whose lease lapsed while the
  Bluetooth link was down, so `expire_outbox` moved it to the DLQ as `esp32_unreachable` with no
  body; the late dead-lettering `FAIL <id> <reason> <composed body>` must not lose the event-time
  GPS position.
- A critical FAILed with `retry_window` (its 60 s live window ended, possibly before any attempt
  was made) falls under rule 4: attempt 1, moved to `dlq`, body kept. Any token not named in
  rules 1-3 (`bad_response`, `http_<n>`, `no_network`, ...) is rule 4.

`[sync 2]` `cap_unsent` **skips leased rows** (`lease_until_ms > now`): they are neither deleted nor
archived by the cap, since the ESP32 may already have delivered them.

`[sync 2]` DLQ serving is split in two (internal to the Pi, nothing changes on the wire):
`serve_dlq(limit=1)` is what a `DLQ` session uses and bumps `dlq_round` at serve time;
`fetch_dlq()` is a read-only listing in the same order. Read "`fetch_dlq(limit=1)`" below as
`serve_dlq`.

**DLQ order (newest first, round-robin).** `fetch_dlq(limit=1)` returns the non-leased `dlq` row
with the smallest `dlq_round`, ties broken by newest first (`rowid DESC`). Serving a row via
`DLQ` increments its `dlq_round` by 1 (at serve time, whether or not it is later TAKEN). A row
entering the DLQ gets `dlq_round = MIN(dlq_round)` over the other `dlq` rows (0 if none), so it
is served in the current round, ahead of older rows, but can't take several attempts in a row.

**Archive table** `archive`: every column of `alerts` plus `archived_at_ms INTEGER NOT NULL`,
`archive_reason TEXT NOT NULL`. Moving a row = `INSERT` into `archive` + `DELETE` from `alerts`
in one transaction under the write lock. Archived rows are **never deleted, never served, never
resent**. `cap_unsent` no longer deletes: rows over `BUFFER_MAX_UNSENT_ROWS` are dropped
keepalives first (deleted, as today), then the oldest remaining rows are **archived** with reason
`cap_unsent`. `prune_sent` is unchanged (deletes `sent` rows only). `python -m
cv_argus.buffer.status` shows outbox, DLQ (with attempts, round, lease) and the archive (count by
reason + newest rows).

**Migration on open** (idempotent, runs in `_init_schema`): create `archive` and the new columns;
then move to `archive` with reason `migrated_v2`: every `kind='route_status'` row not yet `sent`,
and every `dlq` row that is not a critical event row. Remaining `dlq` rows keep their `attempts`
(capped: a row already at >= 5 is archived with `max_attempts`).

### C1.7 `PartsRuleEngine.reset_signals()` (T1 writes, T2 calls)

```python
def reset_signals(self) -> None: ...
```
Resets every running timer (`_eyes`, `_head`, `_mouth`, `_noface`), the yawn history
(`_yawn_events`, `_yawn_counted`, `_last_yawn_end`) and the recovery timer (`_below_since`).
It does **not** touch the held severity, its reason, `_trigger` or `_seen_face`. So a held
`critical` stays held after a face switch and still needs `RULE_RECOVERY_S` of a clear new face
to drop, and the switch itself never emits a transition. T2 calls it exactly once per face switch
(after `FACE_SWITCH_AFTER_S` = 5 s), together with resetting `PartsTracker`.

`[sync 1]` Face-switch reading and ordering (T2 implements, T1 keeps its comparison as stated):
- The switch timer runs **only while the locked face is missing and a different face is
  continuously visible**. A second face visible while the locked face is still visible never
  starts it (it is ignored). The timer resets when the locked face comes back or when no other
  face is visible.
- On the **switch frame** (the first frame whose switch timer is `>= FACE_SWITCH_AFTER_S`, measured
  with the same frame timestamps the rule engine uses) the face stage still reports the locked
  face as missing (`face_found = False`); the new face is used from the **next** frame.
  `PartsInferenceStage` feeds that frame to the rule engine **first**, then calls
  `reset_signals()` and resets `PartsTracker`.
- The no-face timer starts on or before the switch timer, and the rule engine compares
  `elapsed >= RULE_NO_FACE_CRITICAL_S` (T1: keep `>=`). So when the driver leaves and someone else
  is in view, the no-face CRITICAL fires on or before the switch frame and stays held through the
  switch (C1.7 never clears it); the new face then needs `RULE_RECOVERY_S` of a clear face to
  recover.

`[sync 2]` Face-lock matching, re-acquisition and take-over (T2). This replaces "the lock never
expires" from round 1, which gave a lone driver a false no-face CRITICAL when their box jumped,
and ignored a returning driver for the rest of the trip after a switch to a passenger.
- **Matching.** A detection *matches* a tracked box (the locked face or a bystander) when its
  centre is within `FACE_LOCK_MAX_SHIFT_FRAC` (0.6) x the tracked box side and its side is within
  a factor `FACE_LOCK_MAX_SCALE` (1.6) of it. A matched tracked box is updated to the detection.
- **Bystanders.** On every frame where the locked face matches, every other detection is a
  *bystander* (the bystander set is replaced by them). While the locked face is missing,
  bystanders are carried forward by matching; a bystander unmatched for more than
  `FACE_LOCK_MISS_GRACE_S` (1.0 s) is dropped (so a one-frame detector miss doesn't turn the
  passenger into a "new" face).
- **Locked face missing, per frame:**
  1. Some detection matches **no** bystander (a *new* face: the driver's box jumped, or came back
     displaced after a gap, or anyone after a stretch where no bystander was tracked) -> the lock
     **re-acquires** the largest such detection **on this frame**: `face_found = True` with it, no
     switch, no `reset_signals()`, the switch timer resets.
  2. Only bystanders visible -> `face_found = False`; the switch timer runs as in `[sync 1]`
     (switch frame flagged `face_switched`, lock moves to the largest bystander from the next
     frame, `reset_signals()` + tracker reset).
  3. Nothing visible -> `face_found = False`; switch timer reset.
- **Take-over (locked face visible).** If one other detection is continuously visible with a side
  `>= FACE_LOCK_TAKEOVER_SCALE` (1.25) x the locked face's side for `FACE_SWITCH_AFTER_S` (5 s),
  the lock switches to it with the same switch handling (the take-over frame still reads the old
  locked face, is flagged `face_switched`, the rules see it first, then `reset_signals()` +
  tracker reset; the new face from the next frame). This lets the driver (nearest the camera, so
  largest) take the lock back from a passenger, including when the passenger was the only face at
  boot. A smaller second face is ignored as before.
- Known limit (accepted): a face the system never saw next to the driver is indistinguishable from
  the driver coming back, so it is re-acquired at once (rule 1).
- Required tests (T2): a lone driver whose box jumps more than 0.6 x side has no no-face frame; a
  lone driver who returns displaced after a 2 s gap has no-face only during the gap; driver leaves
  and the passenger (seen together before) stays -> no-face CRITICAL at 5 s and a switch at 5 s
  (unchanged), including with one-frame passenger misses; driver returns 1.3x larger while the
  passenger is locked -> take-over after 5 s, held severity kept; a second face 1.1x larger is
  ignored.

`[sync 3]` Face-lock readings confirmed as built (they replace the literal `[sync 2]` wording where
they differ):
- **Bystander grace also while the locked face matches.** On a frame where the locked face
  matches, the other detections update/add bystanders; a bystander **not** seen on that frame is
  kept until it has been unseen for `FACE_LOCK_MISS_GRACE_S`, not dropped at once. (Otherwise a
  passenger missed on the frame right before the driver leaves would be a "new" face and be
  re-acquired by rule 1.)
- **Rule 3 (nothing visible)** resets the switch timer only when **no bystander is still inside
  its miss grace**; while one is, the timer keeps running, and the switch itself waits for a
  frame with a face to move to. (This is what makes "switch at 5 s including one-frame passenger
  misses" hold.) The no-face timer still starts on or before the switch timer.
- **Take-over "continuously visible"** allows gaps of either face up to `FACE_LOCK_MISS_GRACE_S`;
  the take-over timer runs from the larger face's first sighting and fires only on a frame where
  both faces are visible.
- **Logs.** `ctx.features['face_lock']`, the metrics JSONL key `face_lock` and the per-frame line
  carry `{faces, ignored, switch_s, takeover_s, switched, reacquired}` (`face_lock` is `null` when
  the face stage did not run). Line tokens after `face=`: `faces=<n> ignored=<n>`, then
  ` switch=<s>s` / ` takeover=<s>s` while those timers run, ` SWITCHED` on a switch or take-over
  frame, ` REACQUIRED` on a rule-1 re-acquisition frame.

### C1.8 What the ESP32 does with records (T3, summary)

- Every `PULL` record: update the latest Pi severity (for `vigilance`), then:
  non-event -> `ACK` in the same session; event (C1.5) -> `TAKEN` in the same session and queue it
  in the sender task. `age_s == null` on an event -> `FAIL <id> too_old -`.
- After the sender task finishes an event: `ACK <id>` (created/duplicate), or
  `FAIL <id> <reason> <body>` in the next report session, where `<reason>` is: the backend's
  rejection reason (`route_finished`, `no_route`, `too_old`, `wrong_truck`, `invalid`),
  `retry_window` (medium/OK whose live window ran out), `preempted` (DLQ replay pre-empted), or
  the last transport failure (`no_network`, `no_credentials`, `timeout`, `transport_error`,
  `http_<status>`, e.g. `http_503`) for a critical whose 60 s live window ran out / a failed DLQ
  replay. `<body>` is the composed C2 `event` object for the FAIL that first dead-letters a
  critical, `-` otherwise.
- `DLQ 1` only when `END`'s `dlq_waiting > 0`, the backend is not known unreachable for lack of
  credentials, and **no live event is pending**. A replay uses the stored `dlq.body` (its
  `position`, `speed`, `ai_metadata`, `alert_type`, `severity`, `episode`, `client_event_id`),
  with `age_s` recomputed from the record's `age_s` and `delivery: "dlq"`,
  `queued_seconds: dlq.queued_s` (+ time held on the ESP32). With `dlq.body == null`
  (`esp32_unreachable`), the ESP32 composes the event now from the record, with
  `position: null` (it never saw the event's position).
- `[sync 1]` Lease lapse: `TAKEN` stays **same-session only** (C1.3 unchanged; re-sending `TAKEN`
  for held ids in a later session does nothing, don't rely on it). The ESP32 keeps its sender queue
  **deduplicated by record id**: when the Pi serves (via `PULL` or `DLQ`) a row whose id the ESP32
  already holds, the ESP32 `TAKEN`s it again in that session (allowed: it was just served) and
  keeps **one** copy (the one already queued, with its original live window). The eventual
  `ACK`/`FAIL` covers it whatever state the Pi has moved it to (C1.3 honours ACK/FAIL for leased or
  unleased `outbox`/`dlq` rows). A duplicate POST that slips through is harmless: the backend
  answers `duplicate` (C2.2 rule 5) and the ESP32 ACKs.
- `[sync 2]` Confirmed ESP32 readings (T3 round-1 questions):
  - `MIN_SEND_GAP_MS` is per event: a live event retries no sooner than 1 s after **its own**
    failed attempt; a brand-new event goes at once. A DLQ replay waits 1 s after any failed
    attempt.
  - Any live event also pre-empts an in-flight **points-only** report (not counted; its points go
    back into the track buffer, unlike a *failed* report whose points are dropped).
  - A DLQ row held but not yet in flight when a live event arrives is released at once with
    `FAIL <id> preempted -`.
  - A DLQ replay uses the mobile *retry* timeout (15 s); a points-only report the first-try one.
  - A last-known position restored from NVS after a reboot is used only once the GPS has UTC time
    again (its `location_age_s` must be computable); until then no points, and events carry
    `position: null`.
  - An event without a current fix sends `speed: null`; a `last_known` point sends `speed: 0`.
  - A Pi event record with no `payload.episode` uses the record id as `episode`.
  - A 200 whose `event` part is missing or has an unknown `status`, when an event was sent, is a
    failed attempt with reason `bad_response`.
  - A 200 `rejected` whose `reason` is not one of the five C2.2 reasons is FAILed to the Pi as
    `invalid` (so the Pi archives it rather than retrying it, C1.6 rule 3).
  - Pending ACK/FAIL outcomes are kept until a session's `OK` line arrives and resent otherwise
    (safe per C1.2/C1.6).

---

## C2. ESP32 → backend: `POST /api/devices/me/report`

The **only** device write after the migration. The simulator uses it too.

- Auth: header `X-Device-Api-Key: <key>` (unchanged header). **Device key only** (no JWT
  fallback on this endpoint). The truck is resolved from the key alone (indexed
  `device_api_key_fingerprint`, then one bcrypt check), with a warm-container cache
  fingerprint -> truck id of `DEVICE_AUTH_CACHE_TTL` = 60 s (so a rotate/revoke takes effect on
  other warm containers within 60 s; the container that handled the rotate/revoke invalidates at
  once). Refreshes `device_last_seen_at` as today.
- Content-Type `application/json`. Max body 16 KiB (larger -> 413).

### C2.1 Request

```json
{
  "points": [
    {"age_s": 4.0, "lat": 20.6597, "lon": -103.3496, "speed": 62.5,
     "vigilance": "low", "last_known": false, "location_age_s": 0}
  ],
  "event": {
    "client_event_id": "3f2a9c…",
    "episode": "a1b2c3d4e5f6",
    "kind": "alert",
    "severity": "critical",
    "alert_type": "eyes_closed",
    "age_s": 1.6,
    "delivery": "live",
    "queued_seconds": 0,
    "position": {"lat": 20.6597, "lon": -103.3496, "last_known": false, "location_age_s": 0},
    "speed": 62.5,
    "ai_metadata": {"model": "parts-rules-v1", "scores": {"not_drowsy": 0.05, "drowsy": 0.95}}
  }
}
```

Envelope (a violation -> **422** for the whole request):
- top level is an object; unknown top-level keys are ignored;
- `points`: array, **0..`REPORT_MAX_POINTS` (10)** items, required (may be `[]`);
- `event`: object, or `null`, or absent.

`points[]` item (each validated **individually**; an invalid item is skipped and not stored,
never a 422):

| Field | Type | Rule |
|---|---|---|
| `age_s` | number | 0 <= x <= 604800 (7 days); seconds before the backend received the request |
| `lat` | number | -90..90, finite |
| `lon` | number | -180..180, finite |
| `speed` | number | >= 0, finite, km/h; `0` when `last_known` |
| `vigilance` | string | `low` \| `medium` \| `critical` \| `unknown` |
| `last_known` | bool | `true` = no current fix, this is the saved last good position |
| `location_age_s` | number | >= 0; seconds between that fix and the point (`0` for a live fix) |

The ESP32 sets `vigilance` from the latest Pi record: `none`/`low` -> `low`, `medium`,
`critical`; `unknown` when no Pi record arrived for `PI_STALE_MS` (10 s) or none since boot.
Points are ordered oldest first (largest `age_s` first); one per GPS read (1 s) since the
previous report, the newest 10 kept.

`event` object (validated as a whole; any violation -> per-part result `rejected` / `invalid`,
points are still processed):

| Field | Type | Rule |
|---|---|---|
| `client_event_id` | string | 1..64 chars; the Pi record `id` |
| `episode` | string | 1..64 chars; the Pi `payload.episode` |
| `kind` | string | `alert` \| `ok` |
| `severity` | string\|null | `alert`: `medium` \| `critical` (required); `ok`: `null` or absent |
| `alert_type` | string\|null | `alert`: 1..64 chars, the Pi `payload.reason` (required); `ok`: `null` or absent |
| `age_s` | number | >= 0; seconds between the event and the request. `> 604800` -> `rejected too_old` (not `invalid`) |
| `delivery` | string | `live` \| `dlq` |
| `queued_seconds` | number | >= 0; `live`: `0`; `dlq`: seconds the event waited in the Pi's DLQ |
| `position` | object\|null | `{lat, lon, last_known: bool, location_age_s: number>=0}`; the position when the event was first composed; `null` = position unknown |
| `speed` | number\|null | >= 0 km/h, or `null` |
| `ai_metadata` | object\|null | `{model: string\|null, scores: {not_drowsy: number, drowsy: number}\|null}`; may be `null` (e.g. no scores) |

The backend computes: `received_at = now()` once per request; point `timestamp =
round_to_second(received_at - age_s)`; event `occurred_at = received_at - age_s`
(millisecond precision).

`[sync 3]` **Point timestamps are not rounded to the second**: point `timestamp = received_at -
age_s` truncated to milliseconds, exactly like `occurred_at`. (Rounding made the unique
`(id_route, timestamp)` index silently drop distinct points whenever the request latency differed
by about a second between two consecutive reports, which is normal on GPRS. The ESP32 never
re-sends the points of a failed report, so second-level dedup protected nothing.) The unique index
stays; it now only skips exact duplicates.

`[sync 3]` **The ESP32 measures every `age_s` at send time**, not when it plans the attempt: the
body (points and event) is composed right before `AT+HTTPDATA` on GPRS and right before
`http.POST` on WiFi (after registration, bearer setup and the other AT steps), so only the
request's own transit/TLS time is left unaccounted. `SenderPolicy`'s choice of event and points
is still made when the attempt is planned; only the serialization (and the ages) move.

### C2.2 Response

**401**: missing/unknown/revoked key. **413**: body too large. **422**: envelope invalid (body
`{"detail": ...}`, FastAPI default). **429/5xx**: try again. Otherwise always **200**:

```json
{
  "points": {"stored": 5, "route": "6650…", "status": "ok"},
  "event": {"status": "created", "reason": null, "id_alert": "6651…"},
  "active_route": "6650…"
}
```

| Field | Type | Meaning |
|---|---|---|
| `points.stored` | int | points actually inserted (duplicates and invalid items not counted) |
| `points.route` | string\|null | the route the points went to (the truck's active route), else `null` |
| `points.status` | string | `ok` (truck has an active route), `no_active_route` (none, and its most recently started route is not finished, or it has none), `route_finished` (none, and its most recently started route is `completed`/`cancelled`) |
| `event` | object\|null | `null` when the request had no event |
| `event.status` | string | `created` \| `duplicate` \| `rejected` |
| `event.reason` | string\|null | only for `rejected`: `route_finished` \| `no_route` \| `too_old` \| `wrong_truck` \| `invalid` |
| `event.id_alert` | string\|null | `alert`: the stored (or already stored) alert id; `ok`: the newest alert of that `(id_route, episode)` if one exists yet, else `null` |
| `active_route` | string\|null | the truck's current active route id (`in_progress`/`in_progress_alert`; newest `actual_departure` wins), else `null` |

Points rules: stored on `active_route` only. With no active route nothing is stored and
`points.route` is `null`. A finished route never receives points.

Event rules, in this order:
1. Validation fails -> `rejected invalid`.
2. `age_s > 604800` -> `rejected too_old`.
3. `client_event_id` already exists on an alert or driver-OK of a route of **another truck** ->
   `rejected wrong_truck`.
4. Route **by time**: the truck's route with `actual_departure <= occurred_at` and
   (`actual_arrival` is null **or** `occurred_at <= actual_arrival`); several -> newest
   `actual_departure`. None -> `rejected no_route`. Found but `completed`/`cancelled` ->
   `rejected route_finished` (this includes a DLQ replay of an event that happened during a route
   that has since ended: by design, see plan section 1). `[sync 1]` This applies to `kind: ok`
   exactly as to `kind: alert` (an OK after a stop is `rejected route_finished`).
   `[sync 2]` Precisely: when **no** route contains `occurred_at`, take the truck's newest route
   with `actual_departure <= occurred_at`; if it exists and is `completed`/`cancelled` (the
   event happened after that route ended, e.g. an OK or alert sent after a stop) ->
   `rejected route_finished`; if none exists -> `rejected no_route`. So `no_route` means "the
   truck had not started any route yet when this happened". Both reasons archive on the Pi.
   `[sync 2]` The route's status is **re-read after taking the per-route lock** (in
   `ingest_alert`, `ingest_driver_ok` and `store_points`): if a stop/complete landed in between,
   the event is `rejected route_finished` and the points are not stored
   (`points.status = route_finished`, `stored = 0`).
   Rule 3 needs a lookup by `client_event_id` alone; T4 may add plain (non-unique) indexes on
   `client_event_id` in `alerts` and `driver_oks` (internal, no API change).
5. Dedup: `(id_route, client_event_id)` exists -> `duplicate` (same `id_alert`, nothing changes).
   Concurrent duplicates are resolved by the unique index (insert, catch the duplicate-key error,
   answer `duplicate`).
6. `kind: alert` -> insert `Alert` (C3.2) and run incident grouping + route state (C3.4/C3.5) ->
   `created`. An escalation (same `(id_route, episode)` already has a `medium` alert) gets
   `related_alert_id` = that alert's id. If a driver-OK for `(id_route, episode)` is already
   stored, the new alert gets `driver_ok_at` from it.
7. `kind: ok` -> store a `DriverOk` (`id_route`, `episode`, `client_event_id`, `occurred_at`,
   `received_at`; unique `(id_route, client_event_id)`) and set `driver_ok_at = occurred_at` on
   every alert of `(id_route, episode)` whose `driver_ok_at` is null -> `created`. It never closes
   anything and never changes `incident_state`.

### C2.3 How the ESP32 reacts

| Response | Reaction |
|---|---|
| 200, `event.status` `created`/`duplicate` | the event is delivered: `ACK <id>` to the Pi |
| 200, `event.status` `rejected` | stop retrying it: `FAIL <id> <event.reason> -` (the Pi archives it; C1.6) |
| 200, any `points.status` | points of this report are done (stored or not); set the known active route to `active_route`. While `active_route` is `null`, points-only reports go every `ACTIVE_ROUTE_RETRY_MS` (30 s) instead of every 5 s; events still go immediately |
| 401 | the attempt failed; re-`CONFIG` from the Pi (as today) |
| 413 / 422 | firmware bug: drop the points; the event is treated as `rejected invalid` (`FAIL <id> invalid -`) |
| 429 / 5xx / timeout / transport error | the attempt failed: points dropped, the event follows the retry rules (live window, then DLQ for critical, `retry_window` for medium/OK) |

`[sync 1]` Before the first 200 after boot (or after a re-`CONFIG`) the active route is
**unknown**, not `null`: points-only reports go every `REPORT_INTERVAL_MS` (5 s) until a 200 says
`active_route: null`.

`[sync 2]` **Liveness report with no position.** When the ESP32 has no point to send at all (no fix
this boot and no usable last-known position), it still sends a points-only report with
`points: []` every `ACTIVE_ROUTE_RETRY_MS` (30 s), so `device_last_seen_at` and the known active
route keep updating. With points, the cadence is unchanged (5 s, or 30 s while `active_route` is
`null`).

`[sync 2]` The backend's per-route lock waits at most `ROUTE_LOCK_WAIT` (5 s, below both the 8 s
attempt timeout and the 10 s Lambda timeout) and then answers 503 (a failed attempt, C2.3). Its
lease `ROUTE_LOCK_LEASE` (10 s) must stay `>=` the Lambda timeout.

`[sync 3]` The 5 s wait is **per request**, not per acquisition: a report that takes the route
lock twice (points, then the event) shares one lock deadline, `received_at + ROUTE_LOCK_WAIT`,
across both `route_lock` calls, so it still answers 503 within about 5 s plus the database
round-trips.

`GET /api/devices/me/active-route` stays (unchanged) but the ESP32 no longer needs it.

### C2.4 Legacy device endpoints (T4 keeps them until every team has merged)

`POST /api/alerts`, `POST /api/routes/{id}/status`, `PUT /api/alerts/{id}` (`resolved_at`, device
key) and `POST /api/alerts/{id}/resolve` keep working for the old firmware and the old simulator,
with these changes: `source: panic_button` on create -> **422**; a created medium/critical alert
goes through incident grouping and route state like a report event; a device "resolve" now sets
`driver_ok_at` (the field `resolved_at` is retired) and is **rejected 409 on a finished route**;
a status post with no `vigilance` stores `unknown`. They are deleted in a later cleanup, not in
this work.

`[sync 1]` Clarifications:
- The 409 on a finished route applies to **both** device-resolve paths: `POST
  /api/alerts/{id}/resolve` and the device-key `PUT /api/alerts/{id}` with `resolved_at` (both now
  write `driver_ok_at`). A guardian/root_admin `PUT /api/alerts/{id}` review (notes only) on a
  finished route stays allowed.
- A legacy `POST /api/alerts` with `severity_level: low` is stored with `id_incident: null` and
  does not touch `incident_state` or `last_not_ok_at` (only `medium`/`critical` alerts group).
- `[sync 2]` A **user** (JWT) `PUT /api/alerts/{id}` that sends `resolved_at`, and a JWT
  `POST /api/alerts/{id}/resolve`, are **422** (`"resolved_at is retired; close the incident with
  POST /api/incidents/{id}/close"`). Only the device-key paths still accept `resolved_at` (as
  `driver_ok_at`). The UI never sends it.

---

## C3. Backend → UI

All `GET`s below: any authenticated user (`get_current_user`, JWT). Device keys get 401.

### C3.1 `Route` (model + `RouteOut`), new fields

| Field | Type | Notes |
|---|---|---|
| `incident_state` | `"fine" \| "medium" \| "critical"` | default `fine`; drives the map marker colour |
| `last_not_ok_at` | datetime\|null | newest `occurred_at` of a medium/critical alert or point on this route |
| `stop_reason` | string\|null | set by `POST /stop` |
| `stopped_by` | string\|null | user id |
| `stopped_at` | datetime\|null | |

Internal only (not in `RouteOut`): `ok_since`, `last_ok_point_at` (C3.5), and whatever T4 uses for
the per-route grouping lock.

`PUT /api/routes/{id}` keeps "Start trip"/"Complete trip": moving to `in_progress` sets
`actual_departure = now` if it is null; moving to `completed` sets `actual_arrival = now` if it
is null (the UI may still send them explicitly). Event-to-route matching (C2.2) relies on this.

`[sync 2]` A finished route can't be reopened: a `PUT` on a `completed`/`cancelled` route that
changes `operative_status` is **409** ("A finished route can't be restarted; create a new
route"). Other field edits on a finished route keep working. (The UI never reopens a route.)

`[sync 1]` `POST /api/routes` (create): `RouteCreate` gains optional `actual_departure`
(datetime). A route **created** with `operative_status` `in_progress` or `in_progress_alert` gets
`actual_departure = now` when the body doesn't send one (the simulator creates routes this way).
Created as `completed` -> `actual_arrival = now` if null, same rule as PUT.

`[sync 1]` `DELETE /api/routes/{id}` (unchanged roles) also deletes that route's `incidents` and
`driver_oks`, so no orphan incident stays in the `/api/live` open feed. Its alerts and points are
**not** cascaded (callers such as the simulator reset keep deleting alerts as today).

### C3.2 `Alert` (model + `AlertOut`)

`AlertOut` fields after this work (`*` = new or changed):

| Field | Type | Notes |
|---|---|---|
| `id_alert`, `id_route`, `alert_type`, `source` | string | `source`: `fusion` for every new alert; `panic_button` only on old rows (read-only) |
| `severity_level` | `low\|medium\|critical` | new alerts are `medium`/`critical` |
| `episode` * | string\|null | the Pi episode |
| `id_incident` * | string\|null | `null` only for `low` alerts (old rows, and legacy `low` creates, C2.4) |
| `delivery` * | `"live" \| "dlq"` | default `live`; `dlq` = "recovered after lost connection" |
| `queued_seconds` * | number | default 0 |
| `driver_ok_at` * | datetime\|null | **replaces `resolved_at`** (which is removed from `AlertOut`) |
| `closed_at` * | datetime\|null | set when its incident is closed |
| `coordinates` * | `{lat, lon}`\|null | now nullable |
| `position_unknown` * | bool | `true` iff `coordinates` is null |
| `location_is_last_known` * | bool | `true` = the position is the last known fix, not a live one |
| `location_age_s` * | number\|null | age of that fix at the event time, seconds |
| `speed_at_event` * | number\|null | now nullable, km/h |
| `ai_metadata` * | object\|null | now may be `null` for `fusion` too; `scores` may be `null`; `[sync 2]` `clip_seconds` is optional/nullable (old rows only, absent or `null` on report alerts) |
| `grip_status` | `good\|bad`\|null | no longer sent; `null` on new alerts |
| `timestamp` | datetime | the event time (`occurred_at`) |
| `received_at` | datetime | |
| `time_source` * | `gps\|network\|server\|relative` | `relative` = computed from `age_s` (all report events) |
| `location_source`, `location_accuracy_m`, `client_event_id`, `related_alert_id`, `reviewed_by_operator`, `operator_notes`, `original_severity_level`, `deescalated_by`, `deescalated_at`, `deescalation_reason`, `media_url` | unchanged | |

`validate_source_ai_metadata` is relaxed: `fusion` no longer requires `ai_metadata`/`grip_status`.
Indexes: unique partial `(id_route, client_event_id)` where `client_event_id` is a string;
`(id_route, episode)`; `id_incident`.

### C3.3 `StatusRoute` (GPS points)

New / changed fields: `vigilance: "low" | "medium" | "critical" | "unknown"` (new `unknown`;
**the default is `unknown`, never `low`**), `location_is_last_known: bool` (default false),
`location_age_s: number|null`, `keep: bool`. `odometer` stays (0 for report points).
`delivery`/`queued_seconds`/`driver_ok_at`/`id_incident`/`position_unknown` are **Alert-only**
(points are always live and always have a position).
Indexes: unique partial `(id_route, timestamp)` (duplicates skipped with
`insert_many(ordered=False)`); retention: `keep = true` for the first stored point of each
`(id_route, UTC minute)`, TTL index on `timestamp` with `expireAfterSeconds = 604800` and
`partialFilterExpression {keep: false}`. `StatusRouteOut` adds `location_is_last_known`,
`location_age_s`. `[sync 3]` Report points keep millisecond timestamps (C2.1), so `TrackPoint.t`
can carry milliseconds; the UI's `(id_route, t)` de-duplication is unchanged.

### C3.4 `Incident` (new collection `incidents`) and grouping

```ts
interface Incident {
  id_incident: string
  id_route: string
  severity: 'medium' | 'critical'          // max severity_level of its alerts
  tier: 'critical' | 'medium' | 'old'      // computed at response time
  first_alert_at: string                    // min alert timestamp
  last_alert_at: string                     // max alert timestamp
  alert_count: number
  driver_ok_at: string | null               // driver_ok_at of the episode of its newest alert
  has_replayed: boolean                     // any alert with delivery 'dlq'
  closed_at: string | null
  closed_by: string | null                  // user id
  close_note: string                        // '' when none
  route_ended: boolean                      // route is completed or cancelled (now)
  truck_plate_number: string | null
  driver_full_name: string | null
  alerts: Alert[]                           // timestamp ascending, full AlertOut (with position)
}
```

Grouping (on every new medium/critical alert, serialized per route with an atomic
`findOneAndUpdate` on the Route): the alert joins the route's **open** incident `I` for which
`I.first_alert_at - 15 min < occurred_at < I.last_alert_at + 15 min`
(`INCIDENT_GROUP_WINDOW`); if several match, the one with the newest `last_alert_at`. Otherwise a
new incident is created. Joining updates `first_alert_at`/`last_alert_at` (min/max),
`alert_count`, `severity` (max), `has_replayed`, `driver_ok_at`. A closed incident never gets new
alerts. A de-escalation (`POST /api/alerts/{id}/de-escalate`, unchanged) recomputes its
incident's `severity`. `[sync 2]` The incident severity never goes below `medium`: if every
alert was de-escalated to `low`, the incident stays open with `severity: medium`.

`[sync 3]` `closed_by` is a real user id or **`null`**. `null` means "closed by the system": every
incident the data migration closes (alerts all reviewed, or `--close-ended-routes`) gets
`closed_by: null` plus an explanatory `close_note`; no placeholder string such as `"migration"`
is ever stored. The UI must not assume `closed_by` resolves to a user.

`[sync 3]` Migration `--close-ended-routes` keeps its scope: it closes only the incidents created
in the same run, so the choice is made before the first real run (deploy checklist, plan section
6 step 1). No marker field is added.

Tier at response time: `old` if `now - last_alert_at > OLD_INCIDENT_AFTER` (1 h), else its
`severity`. Feed order: tier (`critical`, `medium`, `old`), then `last_alert_at` descending. The
UI shows tier sections and, inside a section, groups incidents of the same route under one route
header (routes ordered by their newest incident).

### C3.5 Route `incident_state`

- On a new medium/critical alert: `incident_state = max(incident_state, severity)`,
  `last_not_ok_at = max(last_not_ok_at, occurred_at)`, `ok_since = null`.
- On stored points, in timestamp order: `vigilance == low` -> if `ok_since` is null or
  `t - last_ok_point_at > OK_MAX_GAP` (60 s) then `ok_since = t`; `last_ok_point_at = t`.
  Any other vigilance (`medium`, `critical`, `unknown`) -> `ok_since = null`; `medium`/`critical`
  also update `last_not_ok_at`. Then, if `incident_state != fine` and `ok_since` is set and
  `last_ok_point_at - ok_since >= ROUTE_STATE_CLEAR_AFTER` (1 h) -> `incident_state = fine`.
  Silence (no points) never clears anything.
- On closing an incident: `incident_state = min(incident_state, max severity of the route's
  remaining open incidents, or fine if none)`. Closing can only lower the state.
- Alerts and incidents stay open when the state clears.
- `[sync 1]` Points **never raise** `incident_state`: a `medium`/`critical` point (e.g. a medium the
  ESP32 gave up on) only updates `last_not_ok_at` and breaks `ok_since`. Only alerts raise it.
- `[sync 2]` Late points (confirmed T4 reading): a `low` point older than the newest OK point
  already counted is ignored for the OK run; a non-`low` point breaks the run only if it is at or
  after `ok_since`. Legacy `POST /status` points go through the same path.

### C3.6 Endpoints

**`GET /api/live`** — query: `since` (ISO datetime, optional), `route` (route id, optional).

```ts
interface LiveResponse {
  server_time: string            // pass back as `since` next time
  routes: RouteLive[]
  incidents: Incident[]          // ALL open incidents in scope, full, feed-ordered (C3.4)
  track: TrackPoint[]            // ordered by id_route, then t ascending
}
interface RouteLive extends Route {          // Route incl. the C3.1 fields
  truck_plate_number: string | null
  driver_full_name: string | null
  latest_position: LivePosition | null       // newest point by timestamp
}
interface LivePosition {
  t: string; lat: number; lon: number; speed: number
  vigilance: 'low' | 'medium' | 'critical' | 'unknown'
  last_known: boolean; location_age_s: number | null
}
interface TrackPoint extends LivePosition { id_route: string }
```
- Without `route`: `routes` = active routes (`in_progress`, `in_progress_alert`); `incidents` =
  every open incident of any route (an ended route's open incidents included, with
  `route_ended: true`); `track` = points of the active routes.
- With `route=<id>`: `routes` = that one route (any status; 404 if it doesn't exist);
  `incidents` = that route's open incidents; `track` = that route's points.
- `track`: without `since`, points with `timestamp >= server_time - LIVE_TRACK_INITIAL_WINDOW`
  (15 min); with `since`, points with `received_at > since` (a `since` older than 15 min is
  clamped to 15 min). Malformed `since` -> 422. The UI appends and de-duplicates by
  `(id_route, t)`. Incidents and routes are always returned in full (no delta).
- `[sync 1]` UI merge rules: the UI **trusts the server `tier`** and does not recompute `old`
  between polls (it may only re-sort). Without a `route` filter, after each merge the UI keeps
  only track points whose `id_route` is in the latest `routes` array (a stopped/completed route's
  path disappears from Live ops; the route view uses `?route=` or `/track`). With `route=<id>` it
  keeps that route's points.
- `[sync 2]` `server_time` is taken **before** any query, and with `since` the backend returns
  points with `received_at > since - LIVE_SINCE_OVERLAP` (10 s), so a point whose report was
  received just before the previous `server_time` but committed after that poll's query is not
  lost. Overlap is harmless: the UI de-duplicates by `(id_route, t)`.
- `[sync 2]` The **route view** draws the whole path for any route, active or ended: it seeds from
  `GET /api/routes/{id}/track` once, then folds the `/api/live?route=<id>` track deltas in with
  the same `(id_route, t)` de-duplication (`/api/live` without `since` only returns 15 min).
- `[sync 2]` UI-only memory cap: at most `LIVE_TRACK_MAX_POINTS_PER_ROUTE` (10000) points per
  route, newest kept.

**`GET /api/routes/{id}/track?max_points=2000`** (`max_points` 1..10000, default
`TRACK_MAX_POINTS` 2000): `{ id_route: string, downsampled: boolean, points: TrackPoint[] }`,
`t` ascending, any route status. When there are more points than `max_points`, keep the first and
last and every point whose `vigilance != low`, then an even stride of the rest.

**`GET /api/incidents?route_id=&status=open|closed|all&limit=100`** (`limit` 1..500, default
100, `status` default `all`) -> `Incident[]` ordered by `last_alert_at` descending.
**`GET /api/incidents/{id}`** -> `Incident`.

**`POST /api/incidents/{id}/close`** — body `{ "note": string }` (max
`CLOSE_NOTE_MAX_LEN` 1000; stripped). Roles: **guardian, root_admin** (admin and truck_driver
403; no token 401). 404 unknown id. 409 already closed. 422 if `severity == critical` and the
stripped note is empty (`"A note is required to close a critical incident"`); for a `medium`
incident the note may be empty. Sets `closed_at = now`, `closed_by`, `close_note`; every alert of
the incident gets `closed_at` and `reviewed_by_operator = true`; route state per C3.5. Returns
the `Incident`.

**`POST /api/routes/{id}/stop`** — body `{ "reason": string }` (1..`STOP_REASON_MAX_LEN` 500 after
strip; empty -> 422). Roles: **root_admin, admin** (guardian/truck_driver 403). Allowed from
`scheduled`, `in_progress`, `in_progress_alert`; `completed`/`cancelled` -> 409. Sets
`operative_status = cancelled`, `stop_reason`, `stopped_by`, `stopped_at = now`, and
`actual_arrival = now` if the route had departed (`actual_departure` set) and `actual_arrival`
is null. Open incidents stay open. Returns `RouteOut`. From then on the device gets
`points.status = route_finished` and events of that route `rejected route_finished`.

`[sync 3]` Pinned texts and the lock: an empty/blank reason is 422 and the response body contains
`"A reason is required to stop a route"`. `POST /stop` and a status-changing `PUT /api/routes/{id}`
("Complete trip") take the per-route lock; under contention they answer **503 `"Route is busy, try
again"`** — the UI shows it as a retryable error (dialog stays open), not as a failed stop.

**Existing endpoints kept:** `GET /api/routes/active` (unchanged shape plus the C3.1 fields in
each route; the UI moves to `/api/live`), `GET /api/alerts` (+ the C3.2 fields; new query param
`incident_id`), `PUT /api/alerts/{id}` review (guardian/root_admin; notes only — closing is the
incident endpoint), `POST /api/alerts/{id}/de-escalate`.

### C3.7 UI types (T5) and labels

`types.ts` follows C3.1–C3.6 exactly (`Vigilance = 'low'|'medium'|'critical'|'unknown'`,
`IncidentState = 'fine'|'medium'|'critical'`, `TimeSource` gains `'relative'`, `Alert.resolved_at`
replaced by `driver_ok_at`). `ALERT_TYPE_LABEL` covers the C1.4 `reason` values that can be an
alert type: `eyes_closed` "Eyes closed", `yawn_eyes_closed` "Eyes closed after yawning",
`head_down` "Head down", `head_down_eyes_closed` "Head down, eyes closed", `no_face` "Driver not
visible", plus `panic_button` "Panic button" for old rows; unknown values show the raw string.
Position label: `position_unknown` -> "Position unknown"; `location_is_last_known` ->
"Last known (N old)" with N formatted from `location_age_s`. Replayed badge when
`delivery == 'dlq'` ("Recovered after lost connection").

`[sync 3]` UI test hooks (it-argus relies on these; T5 keeps them, T6 uses them):
- Tier sections are `role="region"` with accessible names `Critical incidents`, `Medium
  incidents`, `Older than 1 h incidents` (the visible header may add a count, e.g. `Older than 1 h ·
  1`, so tests use the region name, not exact header text).
- After a successful **Close incident** (Live ops and the route view, with or without a focused
  alert), the UI shows a confirmation in an element with `role="status"` whose text contains
  `Closed HH:MM` (the close time, same format as the closed pill). It stays until the next close
  or navigation (no auto-dismiss), and the incident leaves the Open list. The view does not switch
  tabs on its own.
- Close dialog: `role="dialog"` named `Close incident`, a textbox, confirm button `Close incident`;
  error text `A note is required to close a critical incident`. Stop dialog: `role="dialog"` named
  `Stop route`, a textbox, confirm button `Stop route`; error `A reason is required to stop a
  route`; afterwards the status reads `Stopped`. Complete dialog: `role="dialog"` named `Complete
  trip`.
- Each incident card is a container a test can scope to (filter by its alert-type label), and holds
  its own `Close incident` button; the route view's `Open`/`Closed` filter chips are buttons, not
  incident state.

---

## C4. Constants

| Name | Value | Defined in (owner) | Notes |
|---|---|---|---|
| `RULE_NO_FACE_CRITICAL_S` | 5.0 | `cv-argus/src/constants.py` (T1) | was 10.0 |
| `RULE_NO_FACE_S`, `RULE_NO_FACE_MEDIUM_S` | — | removed (T1) | no ladder |
| `ORCHESTRATOR_KEEPALIVE_SECONDS` | 2.0 | `constants.py` (T1) | unchanged |
| `ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS` | — | removed (T1) | |
| `BUFFER_LEASE_SECONDS` | 120.0 | `constants.py` (T1) | `TAKEN` lease |
| `BUFFER_DLQ_MAX_ATTEMPTS` | 5 | `constants.py` (T1) | the plan's `DLQ_MAX_ATTEMPTS` |
| `BUFFER_ARCHIVE_NOW_REASONS` | `("route_finished","no_route","too_old","wrong_truck","invalid")` | `constants.py` (T1) | C1.6 |
| `BUFFER_MAX_UNSENT_ROWS` | 20000 | `constants.py` (T1) | unchanged; overflow now archives |
| `DEFAULT_OUTBOX_MAX_AGE_SECONDS` | 60.0 | `sender/server.py` (T1) | unchanged |
| `MAX_PULL_LIMIT` | 50 | `sender/protocol.py` (T1) | unchanged |
| `MAX_DLQ_LIMIT` | 1 | `sender/protocol.py` (T1) | was 10 |
| `FACE_SWITCH_AFTER_S` | 5.0 | `constants.py` (T2) | face lock switch |
| `PARTS_EYE_MAX_FRAC` | 0.30 | `constants.py` (T2) | eye box side / crop side |
| `PARTS_EYE_CY_RANGE` | (0.15, 0.70) | `constants.py` (T2) | eye centre y, crop-normalised |
| `FACE_LOCK_MAX_SHIFT_FRAC` | 0.6 | `constants.py` (T2) | `[sync 2]` match: centre shift / tracked side |
| `FACE_LOCK_MAX_SCALE` | 1.6 | `constants.py` (T2) | `[sync 2]` match: side ratio limit |
| `FACE_LOCK_MISS_GRACE_S` | 1.0 | `constants.py` (T2) | `[sync 2]` bystander kept this long unseen |
| `FACE_LOCK_TAKEOVER_SCALE` | 1.25 | `constants.py` (T2) | `[sync 2]` side ratio for a take-over |
| `PI_BT_POLL_INTERVAL_MS` | 2000 | `esp32_argus/config.h` (T3) | unchanged |
| `PI_BT_PULL_COUNT` | 8 | `config.h` (T3) | unchanged |
| `DLQ_REPLAY_BATCH` | — | removed (T3) | always `DLQ 1` |
| `REPORT_INTERVAL_MS` | 5000 | `config.h` (T3) | after the previous report ends |
| `MIN_SEND_GAP_MS` | 1000 | `config.h` (T3) | between live-event retries |
| `REPORT_MAX_POINTS` | 10 | `config.h` (T3) and `backend-argus/app/constants.py` (T4) | must match |
| `GPS_READ_INTERVAL_MS` | 1000 | `config.h` (T3) | |
| `GPS_LAST_KNOWN_SAVE_MS` | 60000 | `config.h` (T3) | NVS save cadence (also on fix loss) |
| `PI_STALE_MS` | 10000 | `config.h` (T3) | `vigilance: unknown` after this |
| `ATTEMPT_TIMEOUT_WIFI_MS` | 8000 | `config.h` (T3) | |
| `ATTEMPT_TIMEOUT_MOBILE_FIRST_MS` | 8000 | `config.h` (T3) | |
| `ATTEMPT_TIMEOUT_MOBILE_RETRY_MS` | 15000 | `config.h` (T3) | |
| `MODEM_SETUP_TIMEOUT_MS` | 10000 | `config.h` (T3) | added after power-on / lost signal |
| `LIVE_RETRY_CRITICAL_MS` | 60000 | `config.h` (T3) | then DLQ |
| `LIVE_RETRY_MEDIUM_MS` | 30000 | `config.h` (T3) | then `FAIL retry_window` |
| `LIVE_RETRY_OK_MS` | 30000 | `config.h` (T3) | then `FAIL retry_window` |
| `ACTIVE_ROUTE_RETRY_MS` | 30000 | `config.h` (T3) | points-only cadence with no active route; `[sync 2]` also the empty-points liveness cadence |
| `SENDER_LIVE_SLOTS`, `SENDER_OUTCOME_SLOTS` | 8, 16 | `config.h` (T3) | `[sync 2]` fixed sender slots (bounded string sizes) |
| `DLQ_MAX_ATTEMPTS` | 5 | `config.h` (T3) | `[sync 2]` log line only; must equal `BUFFER_DLQ_MAX_ATTEMPTS` |
| `PANIC_BUTTON_PIN`, `PANIC_BUTTON_DEBOUNCE_MS`, `SMS_DESTINATION_NUMBER`, `SMS_NETWORK_WAIT_MS` | — | removed (T3) | panic removed |
| `INCIDENT_GROUP_WINDOW` | 15 min | `backend-argus/app/constants.py` (T4, new file) | |
| `ROUTE_STATE_CLEAR_AFTER` | 1 h | `app/constants.py` (T4) | |
| `OK_MAX_GAP` | 60 s | `app/constants.py` (T4) | |
| `OLD_INCIDENT_AFTER` | 1 h | `app/constants.py` (T4) | |
| `LIVE_TRACK_INITIAL_WINDOW` | 15 min | `app/constants.py` (T4) | |
| `TRACK_MAX_POINTS` | 2000 | `app/constants.py` (T4) | |
| `GPS_FULL_RETENTION` | 7 days | `app/constants.py` (T4) | then 1 point/min (`keep`) |
| `DEVICE_AUTH_CACHE_TTL` | 60 s | `app/constants.py` (T4) | |
| `CLOSE_NOTE_MAX_LEN` | 1000 | `app/constants.py` (T4) | |
| `STOP_REASON_MAX_LEN` | 500 | `app/constants.py` (T4) | |
| `MAX_EVENT_AGE` | 7 days | `app/schemas/ingestion.py` (T4) | unchanged; = `too_old` |
| `ROUTE_LOCK_WAIT` | 5 s | `app/constants.py` (T4) | `[sync 2]` was 12 s; then 503 |
| `ROUTE_LOCK_LEASE` | 10 s | `app/constants.py` (T4) | `[sync 2]` `>=` the backend Lambda timeout |
| `LIVE_SINCE_OVERLAP` | 10 s | `app/constants.py` (T4) | `[sync 2]` `/api/live?since=` look-back |
| `LIVE_TRACK_MAX_POINTS_PER_ROUTE` | 10000 | `ui-argus` (T5) | `[sync 2]` UI-only memory cap |
| `UI_POLL_MS` | 5000 | `ui-argus` (T5) | `/api/live`, paused while the tab is hidden |
| `ALERT_HISTORY_POLL_MS` | 5000 | `ui-argus` (T5) | |
| `SIMULATOR_POLL_MS` | 10000 | `ui-argus` (T5) | |
| `SIMULATOR_REPORT_INTERVAL_S` | 5 | `simulator-argus` (T6) | mirrors `REPORT_INTERVAL_MS` |
| `SIMULATOR_NORMAL_BLIP_CHANCE` | 0.5 | `simulator-argus` (T6) | `[sync 3]` per-route chance of one head_down medium blip on a `normal` route |
| `SIMULATOR_MEDIUM_BLIP_PROBABILITY` | — | retired (T6) | `[sync 3]` read, ignored, logged as a warning; **not** aliased (a per-5 s rate has no per-route meaning) |
| `EYE_LABEL_MAX_FRAC` | 0.30 | notebook 12 cell 21 (T2) | `[sync 3]` training-only label cleaning; must equal `PARTS_EYE_MAX_FRAC` |

`[sync 3]` Notebook 12's other augmentation constants (`CLOSEUP_EYE_SIDE`, `CLOSEUP_MOUTH_SIDE`,
the `CLOSEUP_*` centre bands, `ZOOM_OUT_P`, `ZOOM_OUT_SCALE`, `BG_NOISE_STD`) are notebook-internal
tuning, not contract: no other module reads them. Notebook 14 mirrors them verbatim.
