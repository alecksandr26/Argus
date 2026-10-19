#!/usr/bin/env python3
"""Summarise a cv-argus metrics log (`OUTPUTS=...,metrics`, one JSON object per frame) for the
report: latency percentiles per pipeline step (and the queueing between steps), how often each
eye/mouth/head state and severity was seen, how many alert episodes fired, and CPU (per core) /
RAM usage over the run.

    docker compose cp cv-argus:/app/data/metrics.jsonl .
    python scripts/summarize_metrics.py metrics.jsonl            # text tables
    python scripts/summarize_metrics.py metrics.jsonl --csv out  # + out_latency/_states/_resources.csv

Stdlib only (no numpy), so it runs anywhere.
"""

import argparse
import csv
import json
import statistics
from collections import Counter

PHASES = ("face", "crop", "infer", "decode", "track", "rules", "parts_stage")
SEVERITIES = ("none", "low", "medium", "critical")


def pct(values, q):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def summarize(recs):
    lat = {p: [r["timing_ms"][p] for r in recs if p in r.get("timing_ms", {})] for p in PHASES}
    lat["queue"] = [r["queue_ms"] for r in recs if r.get("queue_ms") is not None]
    lat["e2e"] = [r["e2e_ms"] for r in recs if r.get("e2e_ms") is not None]
    latency = [{"phase": p, "n": len(v), "mean_ms": statistics.fmean(v) if v else float("nan"),
                "p50_ms": pct(v, 0.5), "p95_ms": pct(v, 0.95), "p99_ms": pct(v, 0.99),
                "max_ms": max(v) if v else float("nan")} for p, v in lat.items()]

    face = [r for r in recs if r["face"]]
    states = Counter()
    states.update(f"face={'yes' if r['face'] else 'no'}" for r in recs)
    states.update(f"eyes={'closed' if r['eyes_closed'] else ('open' if r['eyes'] else 'not seen')}" for r in face)
    states.update(f"mouth={r['mouth'] or 'closed'}" for r in face)
    states.update(f"head={r['head'] or 'not seen'}" for r in face)
    states.update(f"severity={r['severity']}" for r in recs)
    states.update(f"reason={r['reason']}" for r in recs if r["severity"] != "none")

    # Alert episodes: entering medium/critical from below.
    episodes, prev = Counter(), "none"
    for r in recs:
        rank = SEVERITIES.index(r["severity"])
        if rank >= 2 and rank > SEVERITIES.index(prev):
            episodes[f"{r['severity']}:{r['reason']}"] += 1
        prev = r["severity"]

    span_s = (recs[-1]["t_ms"] - recs[0]["t_ms"]) / 1000.0 if len(recs) > 1 else 0.0
    return latency, states, episodes, span_s


def summarize_resources(recs):
    """Mean / p95 / max of the CPU and RAM readings (`res` in each record, sampled ~1/s).

    Frames between two samples repeat the same reading, so only distinct samples are used:
    consecutive identical `res` dicts count once."""
    samples, prev = [], None
    for r in recs:
        res = r.get("res")
        if res and res != prev:
            samples.append(res)
        prev = res
    series = {"cpu_total_pct": [s["cpu_total"] for s in samples if s.get("cpu_total") is not None],
              "proc_cpu_pct": [s["proc_cpu"] for s in samples if s.get("proc_cpu") is not None],
              "rss_mb": [s["rss_mb"] for s in samples if s.get("rss_mb") is not None],
              "mem_used_pct": [s["mem_used_pct"] for s in samples if s.get("mem_used_pct") is not None]}
    n_cores = max((len(s["cpu_cores"]) for s in samples if s.get("cpu_cores")), default=0)
    for i in range(n_cores):
        series[f"cpu{i}_pct"] = [s["cpu_cores"][i] for s in samples
                                 if s.get("cpu_cores") and len(s["cpu_cores"]) > i]
    return [{"metric": k, "n": len(v), "mean": statistics.fmean(v) if v else float("nan"),
             "p95": pct(v, 0.95), "max": max(v) if v else float("nan")} for k, v in series.items() if v]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--csv", metavar="PREFIX", help="also write PREFIX_latency.csv and PREFIX_states.csv")
    args = ap.parse_args()
    recs = load(args.log)
    if not recs:
        raise SystemExit(f"{args.log}: no records")
    latency, states, episodes, span_s = summarize(recs)
    n = len(recs)
    precision = Counter(r["precision"] for r in recs)
    print(f"{n} frames over {span_s:.1f} s ({n / span_s:.2f} fps)" if span_s else f"{n} frames",
          f"| precision {dict(precision)}")
    print(f"\n{'phase':<12}{'n':>7}{'mean':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}   (ms)")
    for row in latency:
        print(f"{row['phase']:<12}{row['n']:>7}{row['mean_ms']:>9.2f}{row['p50_ms']:>9.2f}"
              f"{row['p95_ms']:>9.2f}{row['p99_ms']:>9.2f}{row['max_ms']:>9.2f}")
    print("\nstate                          frames   share")
    for key in sorted(states):
        group = key.split("=")[0]
        total = n if group in ("face", "severity", "reason") else max(sum(r["face"] for r in recs), 1)
        print(f"{key:<30}{states[key]:>8}  {states[key] / total:>6.1%}")
    print("\nalert episodes (entering medium/critical):", dict(episodes) or "none")
    resources = summarize_resources(recs)
    if resources:
        print(f"\n{'resource':<16}{'samples':>8}{'mean':>9}{'p95':>9}{'max':>9}")
        for row in resources:
            print(f"{row['metric']:<16}{row['n']:>8}{row['mean']:>9.1f}{row['p95']:>9.1f}{row['max']:>9.1f}")
        print("(cpu*_pct: busy % of each core, whole board; proc_cpu_pct: cv-argus itself, % of one core;"
              " rss_mb: cv-argus RAM; mem_used_pct: whole board)")

    if args.csv:
        with open(f"{args.csv}_latency.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(latency[0]))
            w.writeheader(); w.writerows(latency)
        with open(f"{args.csv}_states.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["state", "frames"])
            w.writerows(sorted(states.items()))
        wrote = [f"{args.csv}_latency.csv", f"{args.csv}_states.csv"]
        if resources:
            with open(f"{args.csv}_resources.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(resources[0]))
                w.writeheader(); w.writerows(resources)
            wrote.append(f"{args.csv}_resources.csv")
        print("\nwrote " + ", ".join(wrote))


if __name__ == "__main__":
    main()
