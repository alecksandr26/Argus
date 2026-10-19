"""resources.py: per-core CPU and RAM from /proc, on fake /proc files and a fake clock."""

from cv_argus.pipeline import resources
from cv_argus.pipeline.output_stages import format_parts_line

MEMINFO = "MemTotal:        8000000 kB\nMemFree:  100 kB\nMemAvailable:    6000000 kB\n"
STATUS = "Name:\tpython\nVmRSS:\t  409600 kB\n"


def _stat(cores):
    """cores: list of (busy, idle) jiffies -> a /proc/stat body."""
    lines = ["cpu  0 0 0 0 0 0 0 0 0 0"]
    for i, (busy, idle) in enumerate(cores):
        lines.append(f"cpu{i} {busy} 0 0 {idle} 0 0 0 0 0 0")
    return "\n".join(lines) + "\n"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _sampler(tmp_path, clock, cores):
    stat, mem, status = tmp_path / "stat", tmp_path / "meminfo", tmp_path / "status"
    stat.write_text(_stat(cores)); mem.write_text(MEMINFO); status.write_text(STATUS)
    s = resources.ResourceSampler(interval_s=1.0, stat_path=str(stat), meminfo_path=str(mem),
                                  status_path=str(status), clock=clock)
    return s, stat


def test_per_core_cpu_ram_and_caching(tmp_path):
    clock = _Clock()
    s, stat = _sampler(tmp_path, clock, [(0, 0)] * 4)
    # One second later: core0 fully busy, core1 half, core2 a quarter, core3 idle.
    stat.write_text(_stat([(100, 0), (50, 50), (25, 75), (0, 100)]))
    clock.t = 1.0
    r = s.current()
    assert r["cpu_cores"] == [100.0, 50.0, 25.0, 0.0] and r["cpu_total"] == 43.8
    assert r["rss_mb"] == 400.0 and r["mem_used_pct"] == 25.0 and r["mem_avail_mb"] == 5859.4
    # Within the interval the cached sample comes back, without re-reading /proc.
    stat.write_text(_stat([(200, 0)] * 4))
    clock.t = 1.5
    assert s.current() is r


def test_too_short_a_window_reports_no_cpu_and_retries(tmp_path):
    """A replayed video file can hit the sampler twice within milliseconds: that's no CPU
    reading (not 0%), and the next frame tries again."""
    clock = _Clock()
    s, stat = _sampler(tmp_path, clock, [(0, 0)] * 2)
    clock.t = 0.05
    r = s.current()
    assert r["cpu_cores"] is None and r["rss_mb"] == 400.0      # RAM is fine at any window
    stat.write_text(_stat([(30, 70), (60, 40)]))
    clock.t = 0.3                                               # within interval_s, but retried
    assert s.current()["cpu_cores"] == [30.0, 60.0]


def test_missing_proc_gives_none(tmp_path):
    s = resources.ResourceSampler(stat_path=str(tmp_path / "no"), meminfo_path=str(tmp_path / "no"),
                                  status_path=str(tmp_path / "no"), clock=_Clock())
    r = s.current()
    assert r["cpu_cores"] is None and r["rss_mb"] is None and r["mem_used_pct"] is None


def _rec(res):
    return {"source": "pi_camera", "severity": "none", "reason": "ok", "trigger_s": 0.0,
            "face": True, "eyes": ["open_eye", "open_eye"], "mouth": None, "head": "head_straight",
            "timers": {}, "timing_ms": {"face": 6.8, "infer": 0.7}, "queue_ms": 0.3,
            "e2e_ms": 8.0, "res": res}


def test_log_line_shows_cores_and_ram():
    line = format_parts_line(_rec({"cpu_cores": [12.0, 30.0, 25.0, 26.0], "cpu_total": 23.2,
                                   "proc_cpu": 45.0, "rss_mb": 412.0, "mem_used_pct": 18.0}))
    assert line.endswith("| cpu 23% [12 30 25 26] proc 45% | ram 412MB sys 18%")
    assert "ms blazeface 6.8 infer 0.7 queue 0.3 e2e 8.0" in line


def test_log_line_without_proc_omits_resources():
    line = format_parts_line(_rec({"cpu_cores": None, "rss_mb": None}))
    assert "cpu" not in line and "ram" not in line
