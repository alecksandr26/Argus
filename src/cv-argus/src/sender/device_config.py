"""`DeviceConfig` — this truck's backend credentials, which the Pi holds and the ESP32 fetches
over Bluetooth (`CONFIG`, see `protocol.py`).

The Pi never calls the backend itself: the ESP32 is the device that does (root `CLAUDE.md`).
The credentials still live here because the Pi is the provisioned, re-deployable half of the
truck: the dashboard's Fleet screen issues the key and hands it out as an `argus-device.env`
file, which `docker-compose.yml` loads into this container. That keeps the ESP32 firmware
generic, with no per-truck key flashed into it.

The key is a secret: `repr()` masks it, and nothing in this module logs it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

TRUCK_ID_ENV = "ARGUS_TRUCK_ID"
DEVICE_API_KEY_ENV = "ARGUS_DEVICE_API_KEY"
BACKEND_URL_ENV = "ARGUS_BACKEND_URL"
_ENV_VARS = (TRUCK_ID_ENV, DEVICE_API_KEY_ENV, BACKEND_URL_ENV)


@dataclass(frozen=True)
class DeviceConfig:
    truck_id: str
    device_api_key: str = field(repr=False)
    backend_url: str

    def to_wire(self) -> dict[str, str]:
        """The `CONFIG` response payload. Field names match the ESP32's `device_config.cpp`."""
        return {
            "truck_id": self.truck_id,
            "device_api_key": self.device_api_key,
            "backend_url": self.backend_url,
        }

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeviceConfig | None:
        """Reads the three `ARGUS_*` variables. `None` when none is set (an unprovisioned truck:
        the ESP32 gets `NOCONFIG`). Raises `ValueError` when only some are set, since a partial
        config is a provisioning mistake that should fail at startup, not on the road."""
        env = os.environ if environ is None else environ
        values = {name: env.get(name, "").strip() for name in _ENV_VARS}
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                f"Incomplete device credentials: {', '.join(missing)} not set. Download a "
                "fresh argus-device.env from the dashboard's Fleet screen."
            )
        return cls(
            truck_id=values[TRUCK_ID_ENV],
            device_api_key=values[DEVICE_API_KEY_ENV],
            backend_url=values[BACKEND_URL_ENV].rstrip("/"),
        )
