"""Thin async wrapper around backend-argus's REST API.

Every request/response shape here is copied verbatim from backend-argus's own Pydantic schemas
(`app/schemas/*.py`) -- there is no separate contract to design for this module, just the real
one to reuse. See this module's CLAUDE.md for why.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

import httpx


def sha256_hex(raw_password: str) -> str:
    """backend-argus's `Sha256HexDigest` contract: every password field on its API boundary is
    the SHA-256 hex digest of the real password, computed client-side (normally by
    `ui-argus/src/utils/crypto.ts`'s `sha256Hex`) -- never the raw password itself."""
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


class BackendClient:
    def __init__(
        self, base_url: str, transport: Optional[httpx.AsyncBaseTransport] = None
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BackendClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _device(device_key: str) -> dict[str, str]:
        return {"X-Device-Api-Key": device_key}

    # --- provisioning (root_admin JWT) -----------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        resp = await self._client.post(
            "/api/auth/login", json={"email": email, "password": sha256_hex(password)}
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def list_trucks(self, token: str) -> list[dict]:
        resp = await self._client.get("/api/trucks", headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def list_drivers(self, token: str) -> list[dict]:
        resp = await self._client.get("/api/drivers", headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def list_routes(self, token: str) -> list[dict]:
        resp = await self._client.get("/api/routes", headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def list_alerts(self, token: str, route_id: str) -> list[dict]:
        resp = await self._client.get(
            "/api/alerts", params={"route_id": route_id}, headers=self._bearer(token)
        )
        resp.raise_for_status()
        return resp.json()

    async def create_driver(self, token: str, body: dict) -> dict:
        resp = await self._client.post("/api/drivers", json=body, headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def create_truck(self, token: str, body: dict) -> dict:
        resp = await self._client.post("/api/trucks", json=body, headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def create_route(self, token: str, body: dict) -> dict:
        resp = await self._client.post("/api/routes", json=body, headers=self._bearer(token))
        resp.raise_for_status()
        return resp.json()

    async def rotate_key(self, token: str, truck_id: str) -> str:
        resp = await self._client.post(
            f"/api/trucks/{truck_id}/rotate-key", headers=self._bearer(token)
        )
        resp.raise_for_status()
        return resp.json()["device_api_key"]

    async def delete_truck(self, token: str, truck_id: str) -> None:
        resp = await self._client.delete(f"/api/trucks/{truck_id}", headers=self._bearer(token))
        resp.raise_for_status()

    async def delete_driver(self, token: str, driver_id: str) -> None:
        resp = await self._client.delete(f"/api/drivers/{driver_id}", headers=self._bearer(token))
        resp.raise_for_status()

    async def delete_route(self, token: str, route_id: str) -> None:
        resp = await self._client.delete(f"/api/routes/{route_id}", headers=self._bearer(token))
        resp.raise_for_status()

    async def delete_alert(self, token: str, alert_id: str) -> None:
        resp = await self._client.delete(f"/api/alerts/{alert_id}", headers=self._bearer(token))
        resp.raise_for_status()

    # --- ongoing simulated traffic (per-truck device key) -----------------------------------

    async def post_status(self, device_key: str, route_id: str, body: dict) -> dict:
        resp = await self._client.post(
            f"/api/routes/{route_id}/status", json=body, headers=self._device(device_key)
        )
        resp.raise_for_status()
        return resp.json()

    async def post_alert(self, device_key: str, body: dict) -> dict:
        resp = await self._client.post(
            "/api/alerts", json=body, headers=self._device(device_key)
        )
        resp.raise_for_status()
        return resp.json()

    async def resolve_alert(self, device_key: str, alert_id: str, resolved_at_iso: str) -> dict:
        # A device key may set ONLY resolved_at (app/routers/alerts.py's review_alert) --
        # never reviewed_by_operator/operator_notes, which stay a human guardian's job.
        resp = await self._client.put(
            f"/api/alerts/{alert_id}",
            json={"resolved_at": resolved_at_iso},
            headers=self._device(device_key),
        )
        resp.raise_for_status()
        return resp.json()
