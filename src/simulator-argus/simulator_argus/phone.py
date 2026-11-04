"""Guardian phone format (route guardian contracts G2): a copy of backend-argus's
`ALERT_PHONE_PATTERN` (`app/constants.py`) and `normalize_phone` (`app/phone.py`), so the
simulator checks its guardian's phone the way the backend will before it ever sends it."""
from __future__ import annotations

import re

ALERT_PHONE_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")

_SEPARATORS = re.compile(r"[ \-.()]")


def normalize_phone(value: str) -> str:
    """Strips surrounding whitespace, then removes inner spaces, `-`, `.`, `(` and `)`."""
    return _SEPARATORS.sub("", value.strip())


def is_e164(value: str) -> bool:
    return ALERT_PHONE_PATTERN.fullmatch(normalize_phone(value)) is not None
