# Edge-to-cloud scenarios: cv-argus → ESP32 → backend-argus

Every scenario the chain has to handle, the test that pins it, and its status. Built from a
full read-only audit of cv-argus, the ESP32 firmware, backend-argus and the docs (2026-10-17),
after the first real-hardware bench run.

**Status legend**
- 🔴 **bug**: the test asserts the correct behavior and fails today. It's marked
  `xfail(strict=True)` (Python) or listed in `src/esp32-argus/test/host/KNOWN_FAILING` (C++),
  so the suites stay green. A fix makes it pass, which fails the suite until the marker is removed.
- ✅ **covered**: the test passes today. This is new coverage only, not a bug.
- ⏸ **deferred**: a policy question or needs hardware; the reason is given.
- 📄 **docs**: documentation drift, fixed in the docs, no test.

**Where the tests live**
- backend: `src/backend-argus/tests/` (`pytest`)
- cv-argus: `src/cv-argus/tests/` (`pytest`)
- firmware: `src/esp32-argus/test/host/` (`sh run.sh`, host g++)
- chain: `src/tests/edge_chain/` (`pytest`; real cv-argus + simulated ESP32 + real backend)
- hardware: `src/esp32-argus/test/hil/` (the real ESP32, from Windows)

Fusion cases exist twice on purpose: the Python reference (`test_fusion_orchestrator_decision.py`)
and its C++ mirror (`fusion_corner_host_test.cpp`) take the same inputs and expect the same
outputs, so the firmware can't drift from the contract. On top of that, one shared file of
scripted cases (`src/esp32-argus/test/vectors/fusion_vectors.json`, written by
`generate_fusion_vectors.py` from the Python reference) is replayed against `fusion.cpp`
(`fusion_vectors_host_test.cpp`) and against the Python contract the chain sim drives
(`src/tests/edge_chain/test_firmware_vectors.py`) — X-15.

## Top 15

| # | Scenario | Tests |
|---|---|---|
| 1 | cv-argus never sends Not Drowsy, and fusion only runs on Pi records: incidents never resolve, grip-only incidents never fire | `test_orchestrator_readings.py`, `test_drowsy_episode_then_recovery_resolves_incident`, `test_grip_released_while_not_drowsy_raises_medium` |
| 2 | ACK livelock: HTTP while still reading the batch overflows the BT receive buffer; the Pi waits only 10 s for an ACK | HIL (hardware); `test_ack_arriving_in_a_later_session_still_marks_sent` |
| 3 | A record the ESP32 can never relay blocks the head of the queue forever | `ack_policy_host_test`, `pi_record_host_test`, `test_poison_record_does_not_block_records_behind_it` |
| 4 | A failed resolve is forgotten | `test_failed_resolve_is_owed_and_retried_until_confirmed` (+ C++), `test_failed_resolve_is_retried_until_backend_shows_resolved` |
| 5 | An alert posted after recovery is never resolved | `test_alert_posted_after_recovery_owes_a_resolve` (+ C++) |
| 6 | An ESP32 reboot loses the open incident | `test_snapshot_*` (+ C++), `test_esp32_reboot_mid_incident_still_resolves` |
| 7 | Fusion uses ESP32 processing time, not event time | `pi_clock_host_test`, `test_backlog_*` |
| 8 | A stale GPS fix counts as a fix | `location_policy_host_test` |
| 9 | Panic: missed short presses, 240 s SMS block, POST skipped without a fix | `panic_latch_host_test`, `test_panic_during_open_incident_creates_separate_critical` |
| 10 | `GET /api/routes/{id}/status` needs no auth (leaks live location) | `test_get_route_status_without_token_is_401` |
| 11 | Malformed ids / out-of-range coordinates → 500 | `test_ingestion_validation.py` |
| 12 | Retries create duplicate alerts/statuses | `test_idempotency_and_time.py`, `test_lost_ack_re_serve_creates_no_duplicates` |
| 13 | Event time is the server's receive time; datetimes come back without `Z` | `test_idempotency_and_time.py` |
| 14 | Hardcoded `ROUTE_ID`; devices can write to finished routes | `test_route_lifecycle.py` |
| 15 | The real Pi publishes no SDP record for the ESP32 to find | ⏸ needs a real Pi; fix is in `pi-host-setup.sh` + `PI_BT_CHANNEL` |

## cv-argus orchestrator (readings)

| ID | Scenario | Status | Tests |
|---|---|---|---|
| CV-01 | Steady Not Drowsy is never reported | 🔴 | `test_steady_not_drowsy_emits_initial_reading_then_keepalives` |
| CV-02 | No falling edge; flicker handling | 🔴 | `test_drowsy_after_3_frames_emits_one_transition`, `test_not_drowsy_after_3_frames_emits_falling_transition`, `test_flicker_shorter_than_hysteresis_emits_nothing_new` |
| CV-03 | 30 s cooldown hides a continuing episode | 🔴 | `test_keepalive_repeats_state_with_fresh_probabilities`, `test_continuing_drowsy_episode_is_never_silent_for_more_than_keepalive`, `test_keepalive_also_fires_from_tick_when_frames_stop_arriving` |
| CV-04 | No face read as "not drowsy" (a slumped driver) | 🔴 | `test_none_detections_report_no_face_after_hysteresis`, `test_no_face_during_drowsy_episode_never_reports_not_drowsy`, `test_no_face_sustained_counts_as_drowsy` (+ C++), `test_driver_slumps_out_of_frame_raises_an_alert` |
| CV-05 | Stale detector window after a face gap | ⏸ | Needs the TF-backed detector suite (`test_model_fused_detector.py`, Docker tier) |
| CV-06 | Warm-up (mostly padded window) reported | 🔴 | `test_no_readings_during_warm_up_window` |
| CV-07 | Pi stops sending; ESP32 keeps deciding on stale data | 🔴 | `test_stale_camera_reading_stops_new_alerts_and_reports_offline` (+ C++), `test_pi_goes_silent_esp32_stops_raising_from_stale_data` |
| CV-08 | Camera disconnect exits the process | ✅ | `test_pipeline_sources.py` (`TestProduceLive`) + supervisor exit code |
| CV-09 | Runtime model download outside Docker | ⏸ | Dev-only path, documented |
| CV-10 | Orchestrator thread dies silently on an exception | 🔴 | `test_enqueue_error_does_not_kill_the_orchestrator_thread`, `test_supervisor.py` |
| CV-11 | Bridge queue full drops frames | ✅ | `test_full_queue_drops_silently_rather_than_blocking_or_raising` |
| CV-12 | One shared counter across camera sources | 🔴 | `test_two_sources_keep_independent_state` |
| CV-13 | Records carry no monotonic time / boot id / clock-synced flag | 🔴 | `test_alert_carries_mono_ms_boot_id_and_clock_synced`, `test_mono_ms_is_monotonic_across_alerts`, `test_boot_id_is_stable_within_a_process`, `test_clock_fields_are_on_the_wire`, `test_clock_fields_survive_the_buffer`, `test_buffer_file_from_previous_release_opens_and_serves_old_rows` |
| CV-14 | NTP clock step reorders queued rows | 🔴 | `test_fetch_unsent_keeps_insertion_order_when_clock_goes_backwards` |

## cv-argus buffer

| ID | Scenario | Status | Tests |
|---|---|---|---|
| BUF-01 | Unbounded growth | 🔴 | `test_prune_sent_removes_only_old_sent_rows`, `test_unsent_cap_drops_oldest_keepalives_never_transitions` |
| BUF-02 | Restart with an unsent backlog | ✅ | `test_restart_reopens_with_unsent_backlog_oldest_first` |
| BUF-03 | Readers and writer on one connection | ✅ | `test_readers_and_writer_threads_stay_consistent` |
| BUF-04 | Disk full / read-only volume | 🔴 | `test_enqueue_error_does_not_kill_the_orchestrator_thread` |
| BUF-05 | Duplicate enqueue | ✅ | `test_enqueue_is_idempotent_on_id` |

## cv-argus sender (Bluetooth PULL/ACK)

| ID | Scenario | Status | Tests |
|---|---|---|---|
| SND-01/02 | ACK after the 10 s wait / in a later session | 🔴 | `test_ack_arriving_in_a_later_session_still_marks_sent` |
| SND-03 | Client connects and never sends PULL | ✅ | `test_client_that_never_sends_pull_doesnt_block_the_next_one` |
| SND-04 | CRLF line endings | 🔴 | `test_pull_with_crlf_line_ending_is_accepted`, `test_parse_pull_tolerates_trailing_carriage_return` |
| SND-05 | Huge `PULL n` | 🔴 | `test_huge_pull_is_clamped_to_the_protocol_maximum`, `test_parse_pull_clamps_to_max` |
| SND-06 | `PULL 0` | ✅ | `test_parse_pull_zero_is_valid`, `test_empty_pull_sends_only_end_and_no_ack_round_trip` |
| SND-07 | Partial / unknown / duplicate ACK ids | ✅ | `test_partial_ack_leaves_the_rest_unsent`, `test_ack_with_unknown_and_duplicate_ids_reports_actual_count`, `test_parse_ack_rejects_empty_ids_between_commas` |
| SND-08 | Endless line / non-UTF-8 | 🔴 / ✅ | `test_bluetooth_transport_bounds_an_endless_line` (🔴), `test_non_utf8_first_line_does_not_kill_the_accept_loop` (✅) |
| SND-09 | Two clients | ⏸ | One Pi : one ESP32 by design |
| SND-10 | Adapter reset kills the sender thread | 🔴 | `test_listener_error_is_retried_not_fatal`, `test_supervisor.py` |
| SND-11 | No SDP record on the Pi; pairing not scripted | ⏸ | Needs a real Pi (HIL on Windows can't catch it) |
| SND-12 | No Bluetooth passthrough in compose | ⏸ | Needs a real Pi |

## ESP32 Bluetooth client

| ID | Scenario | Status | Tests |
|---|---|---|---|
| EBT-01 | RX overflow while doing HTTP mid-batch | 🔴 | HIL (`esp32_hil.py`, observed on hardware); fix = two-phase pull |
| EBT-02 | Blocking connect delays panic | 🔴 | `panic_latch_host_test` (latch); HIL |
| EBT-03 | Unknown kind / malformed line is never ACKed | 🔴 | `pi_record_host_test` (`unknownKindIsDroppedButAckable`, `malformedJsonIsDroppedWithNoId`) |
| EBT-04 | Missing probabilities read as Not Drowsy | 🔴 | `pi_record_host_test` (`missingOrBadProbabilitiesAreDropped`), `test_poison_record_does_not_block_records_behind_it` |
| EBT-05 | `ACKED n` mismatch ignored | ⏸ | Logging only |
| EBT-06 | Busy-wait `readLine` | ⏸ | Goes away with the comms task |
| EBT-07 | Re-served records re-fused | 🔴 | `pi_clock_host_test` (`recentIdsDropsDuplicates`), `test_lost_ack_re_serve_creates_no_duplicates` |

## Fusion (Python contract + C++ port)

| ID | Scenario | Status | Tests |
|---|---|---|---|
| FUS-01 | Grip-only incident never evaluated | 🔴 | `test_tick_without_a_new_reading_fires_a_grip_only_incident` (+ C++ `tickFiresGripOnlyIncident`), `test_grip_released_while_not_drowsy_raises_medium` |
| FUS-02 | Backlog timing collapses | ✅ contract / 🔴 firmware | `test_backlog_of_spaced_readings_confirms_at_the_right_reading`, `pi_clock_host_test`, `test_backlog_after_30s_offline_relays_with_correct_times` |
| FUS-03 | Out-of-order reading resets timers | 🔴 | `test_reading_older_than_the_last_one_is_ignored` (+ C++) |
| FUS-04 | Debounce/recovery hold across unobserved gaps | 🔴 | `test_gap_longer_than_max_gap_restarts_debounce`, `test_gap_longer_than_max_gap_restarts_recovery` (+ C++) |
| FUS-05 | Failed resolve lost | 🔴 | see Top #4 |
| FUS-06 | Late-posted alert never resolved | 🔴 | see Top #5 |
| FUS-07 | `related_alert_id: ""` instead of null | 🔴 | `test_related_alert_id_empty_string_stored_as_null` |
| FUS-08 | Escalation while flickering medium/low | ✅ (decided: keeps escalating) | `test_medium_low_flicker_still_escalates_on_schedule` |
| FUS-09 | `millis()` wrap / float precision | 🔴 | `pi_clock_host_test` (`survivesMillisWrap`), `epochScaleTimestampsKeepSubSecondPrecision` |
| FUS-10 | Reboot mid-incident | 🔴 | see Top #6 |
| FUS-11 | Grip noise | 🔴 | `grip_debounce_host_test` |
| FUS-12 | Guardian de-escalation invisible to the device | ⏸ | Accepted inconsistency, documented |

## ESP32 GPS, time, location

| ID | Scenario | Status | Tests |
|---|---|---|---|
| GPS-01 | No fix freezes the queue | 🔴 | `location_policy_host_test` (`staleGpsFallsBackToCell`), `test_no_gps_fix_uses_cell_location_marked_cell` |
| GPS-02 | Stale fix | 🔴 | `location_policy_host_test` (`gpsFreshnessBoundary`) |
| GPS-03 | No GPS time | 🔴 | `location_policy_host_test` (`timeSourcePriority`) |
| GPS-04 | Invalid speed → 0 | ⏸ | Acceptable |
| GPS-05 | `BENCH_FAKE_GPS` left on | ⏸ | Boot warning exists; compile guard in the fix |
| GPS-06 | CLBS unavailable / inaccurate / 0,0 | 🔴 | `modem_parse_host_test` (`clbsNoFix`, `clbsRejectsZeroZero`); HIL |

## ESP32 panic and SMS

| ID | Scenario | Status | Tests |
|---|---|---|---|
| PAN-01 | Short press missed during a blocking call | 🔴 | `panic_latch_host_test` |
| PAN-02 | 240 s network wait blocks the POST | 🔴 | HIL |
| PAN-03 | No retry / queue | 🔴 | `panic_latch_host_test` (`queueSurvivesReboot`, `queueIsBounded`) |
| PAN-04 | Network lost after first registration | 🔴 | `modem_parse_host_test` (`creg`) |
| PAN-05 | Panic alert has no resolve path | ✅ backend / 🔴 chain | `test_panic_alert_can_be_resolved_by_guardian`, `test_panic_during_open_incident_creates_separate_critical` |
| PAN-06 | HTTP and SMS on one modem UART | ⏸ | Mutex in the fix; HIL |
| PAN-07 | SIM800L 2 A brownout | 📄 | Hardware note in the README |

## ESP32 network

| ID | Scenario | Status | Tests |
|---|---|---|---|
| NET-01 | 4xx vs 5xx vs transport errors | 🔴 | `ack_policy_host_test`, `test_record_for_a_finished_route_is_dropped_and_acked` |
| NET-02 | 201 with an unparseable body → duplicate | 🔴 | `ack_policy_host_test` (`createdWithUnparseableBodyStillAcks`) |
| NET-03 | No explicit HTTP timeouts | ⏸ | Fix only (constants in `config.h`) |
| NET-04 | Wi-Fi/GPRS flapping | 🔴 | `transport_policy_host_test` |
| NET-05 | GPRS primary not implemented; no PUT; plain HTTP | 🔴 | `transport_policy_host_test`, `modem_parse_host_test`, `test_post_resolve_*` |
| NET-06 | Secrets committed in `config.h` | ⏸ | `secrets.h` split |

## Backend

| ID | Scenario | Status | Tests |
|---|---|---|---|
| BE-01 | GET route status unauthenticated | 🔴 | `test_get_route_status_without_token_is_401`, `test_get_route_status_with_device_key_only_is_401` |
| BE-02 | Malformed ObjectIds → 500 | 🔴 | `test_alert_with_malformed_id_route_is_404_not_500`, `test_route_with_malformed_id_truck_does_not_break_active_routes`, `test_create_route_rejects_nonexistent_truck_and_driver_ids` |
| BE-03 | Out-of-range / non-finite coordinates → 500 | 🔴 | `test_alert_with_out_of_range_coordinates_is_422`, `test_status_with_out_of_range_coordinates_is_422`, `test_non_finite_coordinates_are_422` |
| BE-04 | Device writes to finished routes | 🔴 | `test_device_alert_on_finished_route_is_409`, `test_device_status_on_finished_route_is_409` |
| BE-05 | Device can't find its route | 🔴 | `test_device_active_route_*` |
| BE-06 | No idempotency | 🔴 | `test_same_client_event_id_twice_returns_existing_alert`, `..._status` |
| BE-07 | Event time = receive time | 🔴 | `test_occurred_at_*`, `test_missing_occurred_at_uses_server_time_marked_server`, `test_late_backlog_status_does_not_replace_newer_status` |
| BE-08 | No location source / accuracy | 🔴 | `test_cell_location_with_accuracy_round_trips`, `test_location_source_defaults_to_gps`, `test_unknown_location_source_is_422` |
| BE-09 | No POST resolve (SIM800L can't PUT) | 🔴 | `test_post_resolve_*` |
| BE-10 | Resolve twice overwrites | 🔴 | `test_resolve_twice_keeps_first_resolved_at` |
| BE-11 | `resolved_at` not validated | 🔴 | `test_resolved_at_before_alert_timestamp_is_422`, `test_naive_resolved_at_is_422` |
| BE-12 | Resolving an escalation leaves the original open | 🔴 | `test_resolving_escalation_closes_linked_original`, `test_escalation_chain_links_and_everything_resolves` |
| BE-13 | `related_alert_id` unchecked | 🔴 | `test_related_alert_id_must_exist`, `test_related_alert_id_must_be_on_the_same_route` |
| BE-14 | De-escalating resolved/panic alerts | ⏸ | Policy; current behavior pinned by `test_resolve_then_deescalate_keeps_resolution` |
| BE-15 | Guardian JWT can POST (docs said read-only) | ✅ + 📄 | `test_guardian_jwt_can_post_alert_and_status`; RBAC table fixed |
| BE-16 | Wrong role gets 401 instead of 403 | 🔴 | `test_admin_jwt_posting_alert_is_403_not_401`, `test_truck_driver_jwt_posting_status_is_403_not_401` |
| BE-17 | Cross-truck key replay | ✅ | `test_truck_a_key_on_truck_b_route_is_401_*`, `test_truck_a_key_cannot_resolve_truck_b_alert` |
| BE-18 | Key rotation | ✅ | `test_rotated_key_old_key_is_401_new_key_201` |
| BE-19 | bcrypt blocks the event loop | ⏸ | Performance; fix only |
| BE-20 | Alerts don't update route/truck status | ⏸ | Product decision |
| BE-21 | Orphans after deleting a route | ⏸ | Low |
| BE-22 | No pagination/filters on alerts | ⏸ | Low |
| BE-23 | Unknown fields silently ignored | ⏸ | Turn on `extra="forbid"` only after the firmware sends only known fields |
| BE-24 | Naive datetimes (no `Z`) | 🔴 | `test_timestamps_read_back_from_the_db_are_utc_marked`, `test_status_timestamp_read_back_is_utc_marked` |
| BE-25 | Negative/NaN speed and odometer | 🔴 | `test_negative_speed_and_odometer_are_422`, `test_negative_speed_at_event_is_422` |

## Cross-module contracts

| ID | Contract | Status |
|---|---|---|
| X-01 | Severity enum (`fusion_contract.py`, `fusion.h`, backend `Severity`, `types.ts`) | ✅ aligned, hand-synced |
| X-02 | Threshold 0.57 duplicated in 6 places | ⏸ ESP32 should use the Pi's class; fix with the reading policy |
| X-03 | `alert_type="Not Drowsy"` on grip-only alerts | 🔴 `test_grip_released_while_not_drowsy_raises_medium` |
| X-04..X-08 | Probability index, rank tables, fusion windows, pull limit, timeouts duplicated | ✅ hand-synced; C++/Python mirror tests guard fusion |
| X-09 | Kind strings: a new kind wedges old firmware | 🔴 `pi_record_host_test` |
| X-10/X-11 | Bench ids, BT channel | ⏸ bench-only / needs a Pi |
| X-12 | `""` vs null for `related_alert_id` | 🔴 FUS-07 |
| X-13 | edge_chain hand-built records hid Top #1 | ✅ `test_edge_chain_scenarios.py` drives the real Orchestrator |
| X-14 | Record id as idempotency key unused | 🔴 BE-06 |

## Chain audit, 2026-10-18

A second read-only audit against `main` after the cv-argus reading policy landed (it emits
`No Face`, 2 s keepalives and clock fields; the firmware hasn't changed since the first audit).
Grouped by what each test protects. Chain tests here are `xfail(strict=True,
raises=AssertionError)`, so a known bug only counts when the test's own assertion fails.

**What the camera sends (cv-argus → ESP32)**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| REC-18a | `No Face` (probabilities `null`) is read as p(Drowsy)=0: a driver slumping out of frame recovers the open incident | 🔴 | `test_no_face_mid_incident_does_not_recover` (+ control `test_not_drowsy_after_an_incident_resolves_it` ✅), `no_face_fusion_host_test` |
| REC-18b | Sustained `No Face` from a calm state raises nothing | 🔴 | `test_driver_slumps_out_of_frame_raises_an_alert` (CV-04), `fusion_corner_host_test` (`noFaceSustainedCountsAsDrowsy`) |
| REC-19 | A NaN probability is written as a bare `NaN` token; ArduinoJson 7.4.3 rejects the whole line (`InvalidInput`), so it's never ACKed and blocks the queue | 🔴 | `test_wire_json_is_strict_even_with_nan_probabilities`, `test_nan_detection_is_never_reported_with_nan_probabilities`, `pi_record_edge_host_test`, `test_poison_record_does_not_block_records_behind_it` |
| REC-20 | `clock_synced: false`: `created_at_ms` must never become the event time | ✅ guard / 🔴 parse | `test_unsynced_pi_clock_never_becomes_the_event_time`, `pi_record_edge_host_test` (`clockSyncedFalseIsParsed`) |
| X-16 | The firmware ignores `source_id`: two cameras reset each other's debounce, nothing is raised | 🔴 | `test_two_cameras_do_not_reset_each_others_fusion` |

**Pi health**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| CV-15 | A pipeline that hangs without exiting keeps sending keepalives of the last class: a frozen camera looks like an alert driver | 🔴 | `test_stalled_pipeline_keepalives_stop_or_carry_their_age` (+ `test_keepalive_while_frames_flow_is_not_marked_stale` ✅) |
| BUF-06 | `cap_unsent` only drops keepalives, so transitions and heartbeats grow without bound during a permanent outage (e.g. a rotated key) | 🔴 | `test_unsent_backlog_stays_bounded_without_keepalives` |

**GPS and time on the ESP32**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| GPS-07a | NEO-6M week-rollover dates (1999/2006) are used as GPS time: every resolve 422s, and every record once `occurred_at` is sent | 🔴 | `gps_time_host_test` |
| GPS-07b | Stale GPS time after signal loss counts as valid | 🔴 | `gps_time_host_test` (`staleTimeIsRejected`) |

**Data correctness after a backlog (ESP32 → backend)**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| FUS-15a | Every backlogged status row gets the ESP32's position at relay time | 🔴 | `test_backlog_replay_does_not_stamp_relay_time_position` |
| FUS-15b | A re-served heartbeat carries the vigilance at relay time (calm stored as `critical`) | 🔴 | `test_backlog_heartbeat_keeps_its_own_vigilance` |

**Route lifecycle**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| NET-12 | Route completed and replaced mid-incident: later records get 409 forever; after a refetch an escalation would link across routes (422) | 🔴 | `test_route_change_mid_incident_has_no_stuck_records` |
| BE-26a | Device writes accept a `scheduled` route that `/me/active-route` never returns | 🔴 | `test_a_route_a_device_can_write_to_is_one_it_can_find` |
| BE-26b | A truck in `maintenance`/`inactive` still ingests | ⏸ 🔴 proposed policy, needs a decision | `test_device_writes_for_a_truck_out_of_service_are_409` |

**Connectivity**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| NET-13 | `wifiEnsureConnected()` restarts an attempt still associating every 10 s, so a slow AP/DHCP never connects | 🔴 | `wifi_retry_host_test`; HIL operator step still to add |
| BT-20 | ESP32 resets between END and ACK (half-open link): the Pi waits out its 10 s ACK wait and the next poll fails | 🔴 | `test_esp32_reset_between_end_and_ack_recovers_on_next_poll`; HIL still to add |

**The test harness**

| ID | Scenario | Status | Tests |
|---|---|---|---|
| X-15 | Three hand-kept copies of the ESP32 logic (`fusion.cpp`, `esp32_sim.py`, the HIL `virtual_esp32.py`) can drift | ✅ | `fusion_vectors_host_test`, `test_firmware_vectors.py` (`virtual_esp32.py` joins once the HIL suite is on main) |
| X-17 | The HIL suite runs a scripted Pi, never cv-argus's real `SenderServer` | ⏸ | Needs the HIL suite (`run_suite.py`, uncommitted in the `esp32-hil-suite` worktree) on main |
| X-18 | With 2 s keepalives and 5 s polls, the alert lands between the debounce and one poll later | ✅ guard | `test_debounce_lands_within_one_poll_of_target` |
| X-19 | Hollow chain xfails: `Scenario` called a missing `dev.tick()` and the sim crashed on `No Face`/unknown rows, so known failures failed for the wrong reason (and `drop_next_ack` was never read, so that test XPASSed on main) | ✅ fixed | `esp32_sim.py` now mirrors today's firmware (no-op `tick()`, real lost ACK, ArduinoJson parse rules); `test_poison_record_*` now uses a real poison row (REC-19) |

## Docs drift

| ID | Where | Claim → reality |
|---|---|---|
| D-01..03 | `esp32-argus/README.md`, `docs/roadmap.md`, design overview, borrador, module `CLAUDE.md`s | "firmware not built / never compiled" → compiled, flashed, bench-tested |
| D-04 | root `CLAUDE.md`, esp32 README, `config.h`, `sms_module.h`, roadmap | "Wi-Fi main, SIM800L SMS-only" → GPRS primary, Wi-Fi fallback (decided) |
| D-05 | esp32 README §5, backend `CLAUDE.md` | panic "always POSTs immediately" → skipped without a fix today |
| D-06 | esp32 README §4 | grip "debounced" → it isn't (FUS-11) |
| D-07 | esp32 README §5 | manual de-escalation "not built" → `/de-escalate` exists |
| D-08 | esp32 README §2 | `id_route` "undefined" → hardcoded stopgap (BE-05) |
| D-09 | design overview | "HTTPS + API key" → plain HTTP |
| D-10 | backend `CLAUDE.md` RBAC table | guardian read-only on ingestion → can POST (BE-15) |
| D-11 | `esp32_argus.ino` comment | "retry on the next pull" → resolve was lost (FUS-05) |
| D-12 | `constants.py`, cv-argus `CLAUDE.md` | cooldown rationale → fusion needs a stream |
| D-13 | `config.h` header | "every CHANGE_ME placeholder" → bench values committed |
| D-14 | `fusion_contract.py` docstring | "firmware will implement" → already ported |
