"""Print the Pi's message queue: how much is live, the dead-letter queue (critical alerts the
ESP32 couldn't deliver yet: attempts, replay round, lease) and the archive (what was given up on,
by reason, plus the newest rows).

    docker compose exec cv-argus python -m cv_argus.buffer.status
    docker compose exec cv-argus python -m cv_argus.buffer.status --limit 50 --bodies

Read-only: it never changes the queue (it doesn't serve dead letters, so their replay order is
untouched).
"""

import argparse
import json
import time

from .. import constants
from .store import open_buffer


def _when(epoch_ms: int | None) -> str:
    if not epoch_ms:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch_ms / 1000))


def _what(kind: str, payload: dict) -> str:
    if kind != "drowsiness":
        return kind  # e.g. a migrated route_status heartbeat
    text = f"drowsiness {payload.get('previous_severity', '?')}->{payload.get('severity', '?')}"
    if payload.get("reason"):
        text += f" ({payload['reason']})"
    if payload.get("episode"):
        text += f" episode={payload['episode']}"
    return text


def _reasons(counts: dict) -> str:
    return ", ".join(f"{r or '?'} {n}" for r, n in sorted(counts.items(), key=lambda kv: str(kv[0])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the Pi's message queue (read-only).")
    parser.add_argument("--limit", type=int, default=20,
                        help="dead letters and archived rows to list")
    parser.add_argument("--bodies", action="store_true",
                        help="also print the backend event the ESP32 composed for each dead letter")
    args = parser.parse_args(argv)

    buffer = open_buffer()
    try:
        counts = buffer.counts()
        letters = buffer.list_dlq(limit=args.limit)
        archived = buffer.list_archive(limit=args.limit)
    finally:
        buffer.close()

    print(f"outbox (live, waiting for the ESP32): {counts['outbox']}")
    print(f"dead-letter queue (critical only):    {counts['dlq']}")
    print(f"held by the ESP32 (leased):           {counts['leased']}")
    print(f"delivered (kept for a day):           {counts['sent']}")
    print(f"archive (given up, never resent):     {counts['archived']}")
    if counts["dlq_reasons"]:
        print(f"dead letters, last failure:           {_reasons(counts['dlq_reasons'])}")
    if counts["archive_reasons"]:
        print(f"archived, by reason:                  {_reasons(counts['archive_reasons'])}")

    if letters:
        print()
        print(f"dead letters in replay order (newest first, round-robin; archived after "
              f"{constants.BUFFER_DLQ_MAX_ATTEMPTS} attempts):")
        for d in letters:
            alert = d["alert"]
            body = "with event" if d["body"] else "no event"
            lease = f", leased {d['leased_s']} s" if d["leased_s"] else ""
            print(f"  {_when(alert.created_at_ms)}  {_what('drowsiness', alert.payload)}  | "
                  f"{d['error']}, {d['attempts']} attempt(s), round {d['round']}{lease}, "
                  f"{body}  [{alert.id}]")
            if args.bodies and d["body"]:
                print(f"      {json.dumps(d['body'], separators=(',', ':'))}")

    if archived:
        print()
        print(f"newest {len(archived)} archived row(s):")
        for a in archived:
            error = f", last error {a['last_error']}" if a["last_error"] else ""
            print(f"  {_when(a['archived_at_ms'])}  {_what(a['kind'], a['payload'])}  | "
                  f"{a['reason']}, {a['attempts']} attempt(s){error}  "
                  f"(event {_when(a['created_at_ms'])})  [{a['id']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
