"""CPU (per core) and RAM usage, sampled from `/proc`, for the per-frame logs and metrics.

Stdlib only (no `psutil`, nothing new to install on the Pi), Linux only. On anything without
`/proc/stat` every value comes back `None` and the logs just omit them.

What each number means (inside the container, on the Pi):
- `cpu_cores`: busy % of **each core** since the previous sample, from `/proc/stat`. The
  container sees the host's cores, so on a Pi 5 these are its 4 cores, and they include load
  from anything else running on the board.
- `cpu_total`: the same, averaged over all cores.
- `proc_cpu`: this process's own CPU use since the previous sample, in % of **one** core (so up
  to 400 on a 4-core Pi). It's the share of the load that's actually cv-argus.
- `rss_mb`: this process's resident memory (`VmRSS`), i.e. how much RAM cv-argus holds.
- `mem_used_pct` / `mem_avail_mb`: the board's RAM in use / still available
  (`/proc/meminfo`'s `MemTotal - MemAvailable`).

`ResourceSampler.current()` re-reads `/proc` at most every `interval_s` (default
`RESOURCE_SAMPLE_S`, 1 s) and returns the cached sample otherwise. So calling it on every frame
costs almost nothing, and the CPU percentages cover a meaningful window instead of one 200 ms
frame.
"""

from __future__ import annotations

import os
import threading
import time

_PROC_STAT = "/proc/stat"
_PROC_MEMINFO = "/proc/meminfo"
_PROC_SELF_STATUS = "/proc/self/status"


def _read_cpu_times(path: str = _PROC_STAT) -> dict[str, tuple[int, int]] | None:
    """{"cpu0": (busy, total), ...} jiffies per core, or None if unreadable."""
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return None
    out = {}
    for line in lines:
        name, *vals = line.split()
        if not name.startswith("cpu") or name == "cpu":
            continue
        v = [int(x) for x in vals]
        idle = v[3] + (v[4] if len(v) > 4 else 0)            # idle + iowait
        total = sum(v[:8])                                   # guest time is already in user/nice
        out[name] = (total - idle, total)
    return out or None


def _read_meminfo(path: str = _PROC_MEMINFO) -> tuple[float, float] | None:
    """(used %, available MB) for the whole board."""
    try:
        with open(path) as fh:
            kv = {ln.split(":")[0]: int(ln.split()[1]) for ln in fh if ":" in ln and len(ln.split()) > 1}
    except (OSError, ValueError):
        return None
    total, avail = kv.get("MemTotal"), kv.get("MemAvailable")
    if not total or avail is None:
        return None
    return 100.0 * (total - avail) / total, avail / 1024.0


def _read_rss_mb(path: str = _PROC_SELF_STATUS) -> float | None:
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        pass
    return None


def _proc_cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


class ResourceSampler:
    def __init__(self, interval_s: float = 1.0, stat_path: str = _PROC_STAT,
                 meminfo_path: str = _PROC_MEMINFO, status_path: str = _PROC_SELF_STATUS,
                 clock=time.monotonic):
        self.interval_s = interval_s
        self._paths = (stat_path, meminfo_path, status_path)
        self._clock = clock
        self._lock = threading.Lock()                 # logging + metrics sinks share one sampler
        self._prev_cpu = _read_cpu_times(stat_path)
        self._prev_proc = (_proc_cpu_seconds(), clock())
        self._last_t: float | None = None
        self._sample: dict = {}

    # CPU % over a shorter window than this is noise (a replayed video file can push every frame
    # through in well under a second); report no CPU reading yet and keep the baseline instead.
    MIN_WINDOW_S = 0.2

    def current(self) -> dict:
        with self._lock:
            now = self._clock()
            if self._last_t is None or now - self._last_t >= self.interval_s:
                self._sample = self._take(now)
                if self._sample["cpu_cores"] is not None:     # retry next frame until measured
                    self._last_t = now
            return self._sample

    def _take(self, now: float) -> dict:
        stat_path, meminfo_path, status_path = self._paths
        mem = _read_meminfo(meminfo_path)
        rss = _read_rss_mb(status_path)
        ram = {"rss_mb": round(rss, 1) if rss is not None else None,
               "mem_used_pct": round(mem[0], 1) if mem else None,
               "mem_avail_mb": round(mem[1], 1) if mem else None}
        if now - self._prev_proc[1] < self.MIN_WINDOW_S:
            return {"cpu_cores": None, "cpu_total": None, "proc_cpu": None, **ram}
        cpu = _read_cpu_times(stat_path)
        cores = None
        if cpu and self._prev_cpu:
            cores = []
            for name in sorted(cpu, key=lambda n: int(n[3:])):
                busy, total = cpu[name]
                pbusy, ptotal = self._prev_cpu.get(name, (busy, total))
                dt = total - ptotal
                cores.append(round(100.0 * (busy - pbusy) / dt, 1) if dt > 0 else 0.0)
        self._prev_cpu = cpu
        proc_s, proc_t = _proc_cpu_seconds(), now
        prev_s, prev_t = self._prev_proc
        proc_cpu = round(100.0 * (proc_s - prev_s) / (proc_t - prev_t), 1) if proc_t > prev_t else None
        self._prev_proc = (proc_s, proc_t)
        return {
            "cpu_cores": cores,
            "cpu_total": round(sum(cores) / len(cores), 1) if cores else None,
            "proc_cpu": proc_cpu,
            **ram,
        }


_shared: ResourceSampler | None = None
_shared_lock = threading.Lock()


def shared_sampler() -> ResourceSampler:
    """One process-wide sampler, so every sink reports the same numbers for the same second."""
    global _shared
    with _shared_lock:
        if _shared is None:
            try:
                interval = float(os.environ.get("RESOURCE_SAMPLE_S") or 1.0)
            except ValueError:
                interval = 1.0
            _shared = ResourceSampler(interval_s=interval)
        return _shared
