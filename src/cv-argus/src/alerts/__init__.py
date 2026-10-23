"""`alerts/` — the `Alert` data model + serialization. No logic, no I/O, no persistence: see
`buffer/` for storage and `sender/` for the wire protocol that actually moves these off the Pi.
`orchestrator/`, `buffer/`, and `sender/` all depend on this package; it depends on nothing else
in this project (see `models.py`'s module docstring) — safe to import from anywhere cheaply.
"""

from .models import Alert, AlertKind
from .serialization import age_seconds, from_dict, from_json, to_dict, to_json

__all__ = [
    "Alert",
    "AlertKind",
    "age_seconds",
    "to_dict",
    "from_dict",
    "to_json",
    "from_json",
]
