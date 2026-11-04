"""Phone-number normalisation and the E.164 check shared by `Truck.alert_phone` and guardian
phones (docs/designs/route-guardian-contracts.md, G2)."""
from __future__ import annotations

import re
from typing import Optional

from app.constants import ALERT_PHONE_PATTERN

GUARDIAN_PHONE_ERROR = "guardian phone_number must be in E.164 format, e.g. +523312345678"

_SEPARATORS = re.compile(r"[\s\-.()]")


def normalize_phone(value: str) -> str:
    """Strips surrounding whitespace, then removes inner spaces, `-`, `.`, `(` and `)`:
    `" +52 (33) 1234-5678 "` -> `"+523312345678"`. Doesn't validate anything."""
    return _SEPARATORS.sub("", value.strip())


def is_e164(value: Optional[str]) -> bool:
    """True when `value`, normalised, matches `ALERT_PHONE_PATTERN`."""
    return value is not None and re.fullmatch(ALERT_PHONE_PATTERN, normalize_phone(value)) is not None
