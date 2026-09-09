"""`pipeline/latency.py` — `StageStats`, the per-stage timing/queue-depth/drop instrumentation.

Stdlib only (it stays on the no-`cv2`/`mediapipe`/`tf` import path). Key behaviours: it is a
no-op when the report interval is 0 (except `record_drop`, which keeps counting), a forced
report emits one line and resets the rolling window, and lifetime totals survive that reset.
"""

import logging

from cv_argus.pipeline.latency import StageStats, _clause, _percentile

STATS_LOGGER = "cv_argus.pipeline.stats"


class TestPercentile:
    def test_empty_is_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_element(self):
        assert _percentile([42.0], 95) == 42.0

    def test_nearest_rank(self):
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _percentile(samples, 0) == 10.0
        assert _percentile(samples, 50) == 30.0
        assert _percentile(samples, 100) == 50.0

    def test_p95_rounds_to_nearest_rank(self):
        assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 95) == 50.0


def test_clause_formatting():
    import collections

    text = _clause("proc", collections.deque([10.0, 20.0, 30.0]))
    assert text.startswith("proc(mean=20.0ms ")
    assert "p50=20.0" in text
    assert "max=30.0" in text


class TestDisabledIsNoOp:
    def test_enabled_flag(self):
        assert StageStats("s", 0.0).enabled is False
        assert StageStats("s", 5.0).enabled is True

    def test_records_are_dropped_when_disabled(self):
        stats = StageStats("s", 0.0)
        stats.record_process(0.1)
        stats.record_wait(0.1)
        stats.record_e2e(0.1)
        stats.record_qdepth(3)
        stats.record_phase("embed", 0.05)
        assert stats._proc == stats._wait == stats._e2e
        assert len(stats._proc) == 0
        assert stats._qdepth_n == 0

    def test_drops_still_count_when_disabled(self):
        stats = StageStats("s", 0.0)
        stats.record_drop()
        stats.record_drop(3)
        assert stats._drops == 4

    def test_forced_report_stays_silent_when_disabled(self, caplog):
        stats = StageStats("s", 0.0)
        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report(force=True)
        assert caplog.records == []


class TestEnabledReporting:
    def test_forced_report_emits_one_line_and_resets_window(self, caplog):
        stats = StageStats("face_detector_crop", 10.0)
        for _ in range(5):
            stats.record_process(0.02)
        stats.record_qdepth(4)

        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report(force=True)

        assert len(caplog.records) == 1
        line = caplog.records[0].message
        assert line.startswith("stats face_detector_crop.process: n=5")
        assert "proc(" in line and "drop=" in line
        # window is cleared for the next interval
        assert len(stats._proc) == 0
        assert stats._qdepth_n == 0

    def test_lifetime_totals_survive_the_window_reset(self, caplog):
        stats = StageStats("s", 10.0)
        stats.record_process(0.100)  # 100 ms
        stats.record_process(0.010)
        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report(force=True)

        assert stats._life_count == 2
        assert abs(stats._life_max_ms - 100.0) < 1e-6
        # a second window's report still shows the running lifetime count
        stats.record_process(0.005)
        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report(force=True)
        assert "life(n=3" in caplog.records[-1].message

    def test_phase_clauses_appear_on_the_line(self, caplog):
        stats = StageStats("fused_inference", 10.0)
        stats.record_process(0.05)
        stats.record_phase("embed", 0.03)
        stats.record_phase("lstm", 0.02)
        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report(force=True)
        line = caplog.records[0].message
        assert "embed(" in line and "lstm(" in line

    def test_set_interval_arms_a_stage_built_without_one(self):
        stats = StageStats("s")  # interval defaults to 0.0
        assert stats.enabled is False
        stats.set_interval(10.0)
        assert stats.enabled is True

    def test_report_not_emitted_before_interval_elapses(self, caplog):
        stats = StageStats("s", 3600.0)
        stats.record_process(0.01)
        with caplog.at_level(logging.INFO, logger=STATS_LOGGER):
            stats.maybe_report()  # not forced, interval hasn't elapsed
        assert caplog.records == []
