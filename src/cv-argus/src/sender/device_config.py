"""`DeviceConfig` — this truck's backend credentials, which the Pi holds and the ESP32 fetches
over Bluetooth (`CONFIG`, see `protocol.py`).

The Pi never calls the backend itself: the ESP32 is the device that does (root `CLAUDE.md`).
The credentials still live here because the Pi is the provisioned, re-deployable half of the
truck: the dashboard's Fleet screen issues the key and hands it out as an `argus-device.env`
file, which `docker-compose.yml` loads into this container. That keeps the ESP32 firmware
generic, with no per-truck key flashed into it.

The ESP32's network settings travel the same way, so nothing site-specific is flashed into the
firmware either: the WiFi it falls back to (`ARGUS_WIFI_SSID`/`ARGUS_WIFI_PASSWORD`) and the
SIM's mobile-data APN (`ARGUS_GPRS_APN`/`ARGUS_GPRS_USER`/`ARGUS_GPRS_PASSWORD`; no APN = the
ESP32 doesn't use mobile data). They're optional and set per installation in this container's
`.env`, not in `argus-device.env`, which the dashboard regenerates on every key rotation. Unlike
the credentials they're not `.strip()`ped: an SSID may really end in a space.

The key and passwords are secrets: `repr()` masks them, and nothing in this module logs them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

TRUCK_ID_ENV = "ARGUS_TRUCK_ID"
DEVICE_API_KEY_ENV = "ARGUS_DEVICE_API_KEY"
BACKEND_URL_ENV = "ARGUS_BACKEND_URL"
_ENV_VARS = (TRUCK_ID_ENV, DEVICE_API_KEY_ENV, BACKEND_URL_ENV)

# Optional network settings: wire field name -> env var.
NETWORK_ENV_VARS = {
    "wifi_ssid": "ARGUS_WIFI_SSID",
    "wifi_password": "ARGUS_WIFI_PASSWORD",
    "gprs_apn": "ARGUS_GPRS_APN",
    "gprs_user": "ARGUS_GPRS_USER",
    "gprs_password": "ARGUS_GPRS_PASSWORD",
}


@dataclass(frozen=True)
class DeviceConfig:
    truck_id: str
    device_api_key: str = field(repr=False)
    backend_url: str
    wifi_ssid: str | None = None
    wifi_password: str | None = field(default=None, repr=False)
    gprs_apn: str | None = None
    gprs_user: str | None = None
    gprs_password: str | None = field(default=None, repr=False)

    def to_wire(self) -> dict[str, str]:
        """The `CONFIG` response payload. Field names match the ESP32's `device_config.cpp`;
        unset network settings are left out (the ESP32 then has no WiFi / no mobile data)."""
        payload = {
            "truck_id": self.truck_id,
            "device_api_key": self.device_api_key,
            "backend_url": self.backend_url,
        }
        for name in NETWORK_ENV_VARS:
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

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
        # An empty value means unset; anything else is kept verbatim, spaces included.
        network = {name: env.get(var) or None for name, var in NETWORK_ENV_VARS.items()}
        return cls(
            truck_id=values[TRUCK_ID_ENV],
            device_api_key=values[DEVICE_API_KEY_ENV],
            backend_url=values[BACKEND_URL_ENV].rstrip("/"),
            **network,
        )
