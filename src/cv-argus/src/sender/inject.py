"""Queue one fake drowsiness reading, to test the ESP32 link without the camera:

    docker compose exec cv-argus python -m cv_argus.sender.inject critical
    docker compose exec cv-argus python -m cv_argus.sender.inject none --episode <id>

It goes into the same SQLite queue the running pipeline writes (`BUFFER_DIR`), as a severity
change (`transition: true`), so the ESP32 picks it up on its next poll and treats it like a real
one (contract C1.5): `medium`/`critical` raise an alert, and `none`/`low` with the alert's
`--episode` (previous severity `critical`) is the driver OK for it. Only a critical can end up
in the dead-letter queue; a medium or OK the ESP32 can't deliver is archived. The id printed for a medium/critical reading is that episode. The reading says it was
injected (`reason`, `source_id`), so it can't be mistaken for a detection.
"""

import argparse
import uuid

from .. import constants
from ..alerts import Alert, AlertKind
from ..buffer import open_buffer

SEVERITIES = ("none", "low", "medium", "critical")
_ALERTING = ("medium", "critical")


def build_reading(severity: str, previous: str, episode: str | None, reason: str) -> Alert:
    p_drowsy = float(constants.RULE_P_DROWSY[severity])
    drowsy = severity in _ALERTING
    payload = {
        "severity": severity,
        "reason": reason,
        "model": "injected",
        "class_name": "Drowsy" if drowsy else "Not Drowsy",
        "probabilities": [round(1.0 - p_drowsy, 2), p_drowsy],
        "transition": True,
        "previous_severity": previous,
    }
    if episode:
        payload["episode"] = episode
    return Alert.new(AlertKind.DROWSINESS, level=2 if drowsy else 1, source_id="injected",
                     payload=payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Queue one fake drowsiness reading for the ESP32.")
    parser.add_argument("severity", choices=SEVERITIES)
    parser.add_argument("--previous", choices=SEVERITIES,
                        help="the severity it changes from (default: none, or critical when "
                             "resolving with --episode)")
    parser.add_argument("--episode", help="the episode to continue or resolve (printed by an "
                                          "earlier medium/critical injection)")
    parser.add_argument("--reason", default="injected_test", help="payload.reason (one word)")
    args = parser.parse_args(argv)

    episode = args.episode
    if args.severity in _ALERTING and episode is None:
        episode = uuid.uuid4().hex[:12]
    previous = args.previous or ("critical" if args.episode and args.severity not in _ALERTING
                                 else "none")

    reading = build_reading(args.severity, previous, episode, args.reason)
    buffer = open_buffer()
    try:
        buffer.enqueue(reading)
        counts = buffer.counts()
    finally:
        buffer.close()
    print(f"Queued a fake {args.severity} reading ({reading.id}), previous={previous}"
          + (f", episode={episode}" if episode else ""))
    print(f"The ESP32 gets it on its next poll. Queue: outbox {counts['outbox']}, "
          f"dead-letter queue {counts['dlq']}.")
    if args.severity in _ALERTING:
        print(f"Driver OK for it: python -m cv_argus.sender.inject none --episode {episode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
