"""Parse `cv_argus.pipeline.stats` report lines out of a run's logs into a tidy CSV (and,
optionally, a chart) so a slow pipeline can be picked apart offline instead of squinting at
`docker compose logs`.

Every `LATENCY_LOG_INTERVAL` seconds each stage logs one line like:

    05:26:17 INFO cv_argus.pipeline.stats: stats fused_inference.process: n=17 \
        wait(mean=2938.6ms p50=2933.1 p95=2997.2 max=3024.2) \
        proc(mean=602.3ms p50=598.7 p95=621.5 max=662.0) \
        embed(mean=18.4ms ...) lstm(mean=583.8ms ...) \
        inq(avg=4.0 max=4) drop=0 life(n=252 max=738.7ms)

This turns a whole run of those into one row per (stage, report), with a stable column set —
load it in pandas / a spreadsheet, or pass `--plot` for a quick 2x2 matplotlib overview.

Not imported by `cv_argus` at runtime — a standalone `scripts/` tool, run by hand:

    # straight from a running container (no sudo needed -- `docker logs` reads the json-file
    # driver's file at /var/lib/docker/containers/<id>/<id>-json.log for you):
    python scripts/analyze_latency_logs.py --container cv-argus-cv-argus-1 -o run.csv --plot run.png

    # or from a captured file / a pipe:
    docker logs --since 1h cv-argus-cv-argus-1 > run.log
    python scripts/analyze_latency_logs.py run.log -o run.csv
    docker compose logs --no-color | python scripts/analyze_latency_logs.py - --plot run.png

`--plot` needs `matplotlib` (not a project dependency -- `pip install matplotlib`, or it's
already in `src/dataset`'s `[analysis]` extra); the CSV path needs only the stdlib.
"""

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict

# `stats <stage>.<role>: <body>` -- role is `process` for a consumer stage, `produce` for a source.
_LINE_RE = re.compile(r"stats\s+(?P<stage>[\w.]+?)\.(?P<role>process|produce):\s+(?P<body>.+?)\s*$")
# A `name(k=v k=v ...)` clause, e.g. `proc(mean=602.3ms p50=598.7 ...)` or `inq(avg=4.0 max=4)`.
_CLAUSE_RE = re.compile(r"(\w+)\(([^)]*)\)")
# A `key=number` token (units like the trailing `ms` are simply not captured).
_KV_RE = re.compile(r"(\w+)=(-?\d+(?:\.\d+)?)")
# A leading wall-clock stamp, from our own log format (`%H:%M:%S`) or `docker logs -t` (RFC3339).
_TIME_RE = re.compile(r"(?:^|\s)(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s")

# Clause/metric pairs pulled into fixed columns, in this order. Any other clause found (a new
# phase, say) is appended as `<clause>_<metric>` columns after these.
_COLUMNS = [
    ("proc", ("mean", "p50", "p95", "max")),
    ("wait", ("mean", "p50", "p95", "max")),
    ("embed", ("mean", "p95")),
    ("lstm", ("mean", "p95")),
    ("inq", ("avg", "max")),
    ("e2e", ("mean", "p95")),
]
_BASE_FIELDS = ["time", "elapsed_s", "stage", "role", "n", "fps_est", "drop", "life_n", "life_max"]


def _seconds_since_midnight(match: re.Match) -> float:
    h, m, s = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s


def _iter_stat_rows(lines):
    """Yield a dict per parsed `stats` line: the clause metrics flattened to `clause_metric`
    keys, plus `stage`/`role`/`_time` (seconds-since-midnight or None)."""
    for line in lines:
        m = _LINE_RE.search(line)
        if not m:
            continue
        row = {"stage": m["stage"], "role": m["role"], "_time": None, "time": ""}
        tm = _TIME_RE.search(line[: m.start()])
        if tm:
            row["_time"] = _seconds_since_midnight(tm)
            row["time"] = "{}:{}:{}".format(*tm.groups())

        body = m["body"]
        for clause, inner in _CLAUSE_RE.findall(body):
            for key, val in _KV_RE.findall(inner):
                row[f"{clause}_{key}"] = float(val)
        # Bare `key=value` tokens (n=, drop=) live outside any parens -- strip the clauses first.
        for key, val in _KV_RE.findall(_CLAUSE_RE.sub("", body)):
            row[key] = float(val)
        yield row


def _finalize(rows):
    """Add `elapsed_s` (from the first timestamp seen, midnight-wrap aware) and `fps_est`
    (report's `n` / seconds since that stage's previous report)."""
    first_t = next((r["_time"] for r in rows if r["_time"] is not None), None)
    prev_abs = {}
    prev_report = {}
    for i, r in enumerate(rows):
        t = r["_time"]
        if t is not None and first_t is not None:
            if prev_abs and t < prev_abs["v"] - 43200:  # crossed midnight
                prev_abs["wrap"] += 86400
            t_abs = t + prev_abs.get("wrap", 0)
            prev_abs.update(v=t, wrap=prev_abs.get("wrap", 0))
            r["elapsed_s"] = round(t_abs - first_t, 1)
        else:
            r["elapsed_s"] = float(i)  # no timestamps in the log -- fall back to row index

        key = (r["stage"], r["role"])
        last = prev_report.get(key)
        dt = (r["elapsed_s"] - last) if last is not None else None
        r["fps_est"] = round(r.get("n", 0) / dt, 2) if dt and dt > 0 else ""
        prev_report[key] = r["elapsed_s"]
    return rows


def _write_csv(rows, out):
    extra = []
    for r in rows:
        for k in r:
            if "_" in k and not k.startswith("_") and k not in extra:
                clause = k.split("_")[0]
                if clause not in {c for c, _ in _COLUMNS} and k not in _BASE_FIELDS:
                    extra.append(k)
    fields = list(_BASE_FIELDS)
    for clause, metrics in _COLUMNS:
        fields += [f"{clause}_{mtc}" for mtc in metrics]
    fields += sorted(extra)

    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fields})


def _plot(rows, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_stage = defaultdict(list)
    for r in rows:
        by_stage[r["stage"]].append(r)

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    fig.suptitle("cv-argus pipeline latency", fontsize=13)

    def series(rs, key):
        xs = [r["elapsed_s"] for r in rs if isinstance(r.get(key), (int, float))]
        ys = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
        return xs, ys

    ax = axes[0][0]
    for stage, rs in by_stage.items():
        ax.plot(*series(rs, "proc_mean"), marker=".", label=stage)
    ax.set_title("proc mean (ms) — per stage"); ax.set_xlabel("elapsed s"); ax.set_yscale("log")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    ax = axes[0][1]
    for stage, rs in by_stage.items():
        ax.plot(*series(rs, "wait_mean"), marker=".", label=stage)
    ax.set_title("wait mean (ms) — queue time before each stage"); ax.set_xlabel("elapsed s")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    ax = axes[1][0]
    for stage, rs in by_stage.items():
        xs, ys = series(rs, "e2e_mean")
        if ys:
            ax.plot(xs, ys, marker=".", label=stage)
    ax.set_title("e2e mean (ms) — capture → sink"); ax.set_xlabel("elapsed s")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    ax = axes[1][1]
    plotted = False
    for stage, rs in by_stage.items():
        xs, ys = series(rs, "drop")
        if any(ys):
            ax.plot(xs, ys, marker=".", label=stage)
            plotted = True
    ax.set_title("drop — frames shed per report"); ax.set_xlabel("elapsed s")
    ax.grid(True, alpha=0.3)
    if plotted:
        ax.legend(fontsize=7)
    else:
        ax.text(0.5, 0.5, "no drops", ha="center", va="center", transform=ax.transAxes)

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    print(f"wrote {path}", file=sys.stderr)


def _summary(rows):
    by_stage = defaultdict(list)
    for r in rows:
        by_stage[r["stage"]].append(r)
    print(f"\n{len(rows)} stat reports over {len(by_stage)} stages\n", file=sys.stderr)
    print(f"{'stage':24} {'proc p50':>10} {'wait p50':>10} {'e2e max':>10} {'drop tot':>9}", file=sys.stderr)
    for stage, rs in by_stage.items():
        procs = sorted(r["proc_mean"] for r in rs if "proc_mean" in r)
        waits = sorted(r["wait_mean"] for r in rs if "wait_mean" in r)
        e2es = [r["e2e_mean"] for r in rs if "e2e_mean" in r]
        drops = sum(r.get("drop", 0) for r in rs)
        p = procs[len(procs) // 2] if procs else 0
        wv = waits[len(waits) // 2] if waits else 0
        ev = max(e2es) if e2es else 0
        print(f"{stage:24} {p:>9.1f}m {wv:>9.1f}m {ev:>9.0f}m {drops:>9.0f}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("logfile", nargs="?", help="log file to read, or '-' for stdin")
    src.add_argument("--container", help="run `docker logs <name>` and parse that instead")
    ap.add_argument("--since", help="passed to `docker logs --since` (only with --container)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--plot", metavar="PNG", help="also render a 2x2 overview chart (needs matplotlib)")
    args = ap.parse_args()

    if args.container:
        cmd = ["docker", "logs"]
        if args.since:
            cmd += ["--since", args.since]
        cmd.append(args.container)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr.strip(), file=sys.stderr)
            return 1
        lines = (proc.stdout + proc.stderr).splitlines()  # docker sends app logs to stderr too
    elif args.logfile in (None, "-"):
        lines = sys.stdin.read().splitlines()
    else:
        with open(args.logfile, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()

    rows = _finalize(list(_iter_stat_rows(lines)))
    if not rows:
        print("no `stats ...` lines found -- is LATENCY_LOG_INTERVAL > 0 and the run long enough?", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            _write_csv(rows, fh)
        print(f"wrote {args.out} ({len(rows)} rows)", file=sys.stderr)
    else:
        _write_csv(rows, sys.stdout)

    _summary(rows)
    if args.plot:
        _plot(rows, args.plot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
