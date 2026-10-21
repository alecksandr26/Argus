"""Print the Pi's message queue: how much is live, how much is in the dead-letter queue, and the
dead letters themselves (what, when, why the ESP32 couldn't deliver it).

    docker compose exec cv-argus python -m cv_argus.buffer.status
    docker compose exec cv-argus python -m cv_argus.buffer.status --limit 50 --bodies

Read-only: it never changes the queue.
"""

import argparse
import json
import time

from .store import open_buffer


def _when(epoch_ms: int | None) -> str:
    if not epoch_ms:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch_ms / 1000))


def _what(alert) -> str:
    if alert.kind.value == "route_status":
        return "heartbeat"
    p = alert.payload
    text = f"drowsiness {p.get('previous_severity', '?')}->{p.get('severity', '?')}"
    if p.get("reason"):
        text += f" ({p['reason']})"
    if p.get("episode"):
        text += f" episode={p['episode']}"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the Pi's message queue (read-only).")
    parser.add_argument("--limit", type=int, default=20, help="dead letters to list (oldest first)")
    parser.add_argument("--bodies", action="store_true",
                        help="also print the backend message the ESP32 composed for each")
    args = parser.parse_args(argv)

    buffer = open_buffer()
    try:
        counts = buffer.counts()
        letters = buffer.fetch_dlq(limit=args.limit)
    finally:
        buffer.close()

    print(f"outbox (live, waiting for the ESP32): {counts['outbox']}")
    print(f"dead-letter queue:                    {counts['dlq']}")
    print(f"delivered (kept for a day):           {counts['sent']}")
    if counts["dlq_reasons"]:
        reasons = ", ".join(f"{r or '?'} {n}" for r, n in sorted(counts["dlq_reasons"].items()))
        print(f"why:                                  {reasons}")
    if not letters:
        return 0
    print()
    print(f"oldest {len(letters)} dead letter(s):")
    for alert, dlq in letters:
        body = "with message" if dlq["body"] else "no message"
        print(f"  {_when(alert.created_at_ms)}  {_what(alert)}  | {dlq['error']}, "
              f"{dlq['attempts']} attempt(s), {body}  [{alert.id}]")
        if args.bodies and dlq["body"]:
            print(f"      {json.dumps(dlq['body'], separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
