"""Retries, backlogs and clocks: what happens when the ESP32 re-sends, or relays late.

A lost HTTP response (likely on GPRS) or a lost Bluetooth ACK makes the device send the same
event again; an offline stretch makes it relay a backlog minutes late. See
docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.alert import Alert
from app.models.status_route import StatusRoute
from tests.conftest import auth_headers, fusion_alert_body, status_body


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.xfail(strict=True, reason="BE-06: no client_event_id, a retry duplicates the alert")
async def test_same_client_event_id_twice_returns_existing_alert(api_client, rig_a):
    body = fusion_alert_body(rig_a.route_id, client_event_id="evt-1")
    first = await api_client.post("/api/alerts", json=body, headers=rig_a.headers)
    second = await api_client.post("/api/alerts", json=body, headers=rig_a.headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id_alert"] == second.json()["id_alert"]
    assert await Alert.find(Alert.id_route == rig_a.route_id).count() == 1


@pytest.mark.xfail(strict=True, reason="BE-06: no client_event_id, a retry duplicates the status")
async def test_same_client_event_id_twice_returns_existing_status(api_client, rig_a):
    body = status_body(client_event_id="evt-2")
    url = f"/api/routes/{rig_a.route_id}/status"
    first = await api_client.post(url, json=body, headers=rig_a.headers)
    second = await api_client.post(url, json=body, headers=rig_a.headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert await StatusRoute.find(StatusRoute.id_route == rig_a.route_id).count() == 1


async def test_different_client_event_ids_create_two_alerts(api_client, rig_a):
    for event_id in ("evt-a", "evt-b"):
        resp = await api_client.post(
            "/api/alerts",
            json=fusion_alert_body(rig_a.route_id, client_event_id=event_id),
            headers=rig_a.headers,
        )
        assert resp.status_code == 201
    assert await Alert.find(Alert.id_route == rig_a.route_id).count() == 2


@pytest.mark.xfail(strict=True, reason="BE-07: event time is the server's receive time")
async def test_occurred_at_is_the_alert_timestamp_and_received_at_is_kept(api_client, rig_a):
    occurred = datetime.now(timezone.utc) - timedelta(minutes=10)
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, occurred_at=_iso(occurred), time_source="gps"),
        headers=rig_a.headers,
    )
    alert = resp.json()
    assert alert["timestamp"].startswith(occurred.strftime("%Y-%m-%dT%H:%M"))
    assert alert["time_source"] == "gps"
    assert alert["received_at"] > alert["timestamp"]


@pytest.mark.xfail(strict=True, reason="BE-07: occurred_at isn't validated")
@pytest.mark.parametrize(
    "delta", [timedelta(hours=1), -timedelta(days=30)], ids=["future", "too_old"]
)
async def test_occurred_at_in_future_or_too_old_is_422(api_client, rig_a, delta):
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(
            rig_a.route_id, occurred_at=_iso(datetime.now(timezone.utc) + delta)
        ),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


@pytest.mark.xfail(strict=True, reason="BE-07: no time_source on alerts")
async def test_missing_occurred_at_uses_server_time_marked_server(api_client, rig_a):
    alert = (
        await api_client.post(
            "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=rig_a.headers
        )
    ).json()
    assert alert["time_source"] == "server"


@pytest.mark.xfail(strict=True, reason="BE-07: latest status is by arrival, not event time")
async def test_late_backlog_status_does_not_replace_newer_status(api_client, root_admin, rig_a):
    now = datetime.now(timezone.utc)
    url = f"/api/routes/{rig_a.route_id}/status"
    await api_client.post(
        url,
        json=status_body(vigilance="critical", occurred_at=_iso(now)),
        headers=rig_a.headers,
    )
    # A backlog record from 5 minutes ago arrives after the live one.
    await api_client.post(
        url,
        json=status_body(vigilance="low", occurred_at=_iso(now - timedelta(minutes=5))),
        headers=rig_a.headers,
    )
    latest = (await api_client.get(url, headers=auth_headers(root_admin))).json()
    assert latest["vigilance"] == "critical"


@pytest.mark.xfail(strict=True, reason="BE-08: no location_source/accuracy fields")
async def test_cell_location_with_accuracy_round_trips(api_client, rig_a):
    alert = (
        await api_client.post(
            "/api/alerts",
            json=fusion_alert_body(
                rig_a.route_id, location_source="cell", location_accuracy_m=550.0
            ),
            headers=rig_a.headers,
        )
    ).json()
    assert (alert["location_source"], alert["location_accuracy_m"]) == ("cell", 550.0)


@pytest.mark.xfail(strict=True, reason="BE-08: no location_source/accuracy fields")
async def test_location_source_defaults_to_gps(api_client, rig_a):
    alert = (
        await api_client.post(
            "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=rig_a.headers
        )
    ).json()
    assert alert["location_source"] == "gps"


@pytest.mark.xfail(strict=True, reason="BE-08: unknown location_source isn't rejected")
async def test_unknown_location_source_is_422(api_client, rig_a):
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, location_source="psychic"),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


@pytest.mark.xfail(strict=True, reason="BE-24: datetimes read back from Mongo are naive (no Z)")
async def test_timestamps_read_back_from_the_db_are_utc_marked(api_client, root_admin, rig_a):
    """The UI does `new Date(timestamp)`; without a Z/offset it reads UTC as local time, which
    shifts every alert by the browser's UTC offset (6 h in Mexico)."""
    created = (
        await api_client.post(
            "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=rig_a.headers
        )
    ).json()
    fetched = (
        await api_client.get(f"/api/alerts/{created['id_alert']}", headers=auth_headers(root_admin))
    ).json()
    assert fetched["timestamp"].endswith(("Z", "+00:00"))


@pytest.mark.xfail(strict=True, reason="BE-24: datetimes read back from Mongo are naive (no Z)")
async def test_status_timestamp_read_back_is_utc_marked(api_client, root_admin, rig_a):
    url = f"/api/routes/{rig_a.route_id}/status"
    await api_client.post(url, json=status_body(), headers=rig_a.headers)
    latest = (await api_client.get(url, headers=auth_headers(root_admin))).json()
    assert latest["timestamp"].endswith(("Z", "+00:00"))
