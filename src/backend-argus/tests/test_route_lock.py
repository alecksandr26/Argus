"""The per-route lock (`app.incidents.route_lock`) and its 503 (docs/designs/final-tweaks-contracts.md
C2.3 [sync 2]/[sync 3], C3.6 [sync 3]):

- a contended lock waits at most `ROUTE_LOCK_WAIT`, then answers 503 "Route is busy, try again";
- the wait is per request: a report that takes the lock twice (points, then its event) shares one
  deadline, `received_at + ROUTE_LOCK_WAIT`, so it still answers 503 within about that long;
- `POST /stop` and a status-changing `PUT` ("Complete trip") take the lock and answer the same 503.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import app.incidents as incidents_module
import app.report as report_module
from app.incidents import route_lock
from app.models.alert import Alert
from app.models.common import RouteStatus
from app.models.route import Route
from app.models.status_route import StatusRoute
from tests.conftest import auth_headers, post_report, report_event, report_point

BUSY = "Route is busy, try again"


async def _hold_lock(route_id: str, *, for_: timedelta = timedelta(seconds=10)) -> None:
    """Another request (maybe in another Lambda container) holds the route's lock."""
    route = await Route.get(route_id)
    await Route.get_motor_collection().update_one(
        {"_id": route.id},
        {"$set": {"lock_token": "someone-else", "lock_until": datetime.now(timezone.utc) + for_}},
    )


@pytest.fixture
def short_wait(monkeypatch):
    """ROUTE_LOCK_WAIT shortened so the contended cases run in well under a second."""

    def set_wait(seconds: float) -> timedelta:
        wait = timedelta(seconds=seconds)
        monkeypatch.setattr(incidents_module, "ROUTE_LOCK_WAIT", wait)
        monkeypatch.setattr(report_module, "ROUTE_LOCK_WAIT", wait)
        return wait

    return set_wait


# --- route_lock itself --------------------------------------------------------------------------


async def test_contended_lock_is_503_after_the_wait(rig_a, short_wait):
    short_wait(0.3)
    await _hold_lock(rig_a.route_id)
    started = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        async with route_lock(rig_a.route_id):
            pass
    elapsed = time.monotonic() - started
    assert exc.value.status_code == 503 and exc.value.detail == BUSY
    assert 0.25 <= elapsed < 1.0


async def test_free_lock_is_taken_even_past_the_deadline(rig_a):
    """The lock is always tried once: an uncontended request never gets a 503, however late."""
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    async with route_lock(rig_a.route_id, deadline=past):
        held = await Route.get(rig_a.route_id)
        assert held.lock_until is not None
    assert (await Route.get(rig_a.route_id)).lock_until is None


async def test_contended_lock_past_the_deadline_is_503_at_once(rig_a):
    await _hold_lock(rig_a.route_id)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    started = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        async with route_lock(rig_a.route_id, deadline=past):
            pass
    assert exc.value.status_code == 503
    assert time.monotonic() - started < 0.2


async def test_expired_lease_is_taken_over(rig_a):
    """A holder that crashed (or whose Lambda was killed) frees the lock when its lease ends."""
    await _hold_lock(rig_a.route_id, for_=timedelta(seconds=-1))
    async with route_lock(rig_a.route_id):
        assert (await Route.get(rig_a.route_id)).lock_token != "someone-else"


# --- One deadline per report (C2.3 [sync 3]) ----------------------------------------------------


async def test_report_passes_one_deadline_to_both_lock_acquisitions(api_client, rig_a, monkeypatch):
    received_at = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(report_module, "utcnow", lambda: received_at)
    seen: list[datetime | None] = []

    def spy(original):
        def wrapped(route_id, *, deadline=None):
            seen.append(deadline)
            return original(route_id, deadline=deadline)

        return wrapped

    monkeypatch.setattr(report_module, "route_lock", spy(report_module.route_lock))
    monkeypatch.setattr(incidents_module, "route_lock", spy(incidents_module.route_lock))
    resp = await post_report(api_client, rig_a, points=[report_point()], event=report_event())
    assert resp.status_code == 200, resp.text
    assert seen == [received_at + timedelta(seconds=5)] * 2  # points, then the event


async def test_contended_report_answers_503_within_one_wait(api_client, rig_a, short_wait, monkeypatch):
    """The points take the lock slowly (most of the wait), then another request grabs it before
    the event's turn. The event's acquisition only has what is left of the request's deadline,
    so the report answers 503 after about one ROUTE_LOCK_WAIT in total, not two."""
    wait = short_wait(1.0)
    original = report_module.apply_points_to_route

    async def slow_points(route_id, points):
        await original(route_id, points)
        await asyncio.sleep(0.7)
        await _hold_lock(route_id)  # survives our release: it carries another token

    monkeypatch.setattr(report_module, "apply_points_to_route", slow_points)
    started = time.monotonic()
    resp = await post_report(api_client, rig_a, points=[report_point()], event=report_event())
    elapsed = time.monotonic() - started
    assert resp.status_code == 503
    assert resp.json()["detail"] == BUSY
    # One shared deadline: ~1.0 s. Per-acquisition waits would be 0.7 + 1.0 = 1.7 s.
    assert elapsed < wait.total_seconds() + 0.4
    assert await Alert.find_all().count() == 0
    # The points were stored before the 503; the device drops them anyway (a failed attempt).
    assert await StatusRoute.find(StatusRoute.id_route == rig_a.route_id).count() == 1


async def test_uncontended_slow_report_still_succeeds(api_client, rig_a, short_wait, monkeypatch):
    """Past the deadline but nobody holds the lock: the event is still stored."""
    short_wait(0.2)
    original = report_module.apply_points_to_route

    async def slow_points(route_id, points):
        await original(route_id, points)
        await asyncio.sleep(0.4)

    monkeypatch.setattr(report_module, "apply_points_to_route", slow_points)
    resp = await post_report(api_client, rig_a, points=[report_point()], event=report_event())
    assert resp.status_code == 200, resp.text
    assert resp.json()["event"]["status"] == "created"


# --- Stop and Complete trip under contention (C3.6 [sync 3]) ------------------------------------


async def test_stop_under_contention_is_503_and_changes_nothing(
    api_client, admin_user, rig_a, short_wait
):
    short_wait(0.2)
    await _hold_lock(rig_a.route_id)
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop", json={"reason": "flat tyre"}, headers=auth_headers(admin_user)
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == BUSY
    route = await Route.get(rig_a.route_id)
    assert route.operative_status == RouteStatus.IN_PROGRESS
    assert route.stop_reason is None


async def test_complete_trip_under_contention_is_503_and_changes_nothing(
    api_client, root_admin, rig_a, short_wait
):
    short_wait(0.2)
    await _hold_lock(rig_a.route_id)
    resp = await api_client.put(
        f"/api/routes/{rig_a.route_id}",
        json={"operative_status": "completed"},
        headers=auth_headers(root_admin),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == BUSY
    route = await Route.get(rig_a.route_id)
    assert route.operative_status == RouteStatus.IN_PROGRESS
    assert route.actual_arrival is None


async def test_stop_works_once_the_lock_is_free(api_client, admin_user, rig_a, short_wait):
    """The UI's retry after a 503 (the dialog stays open) goes through once the holder is done."""
    short_wait(0.2)
    await _hold_lock(rig_a.route_id)
    url = f"/api/routes/{rig_a.route_id}/stop"
    busy = await api_client.post(url, json={"reason": "x"}, headers=auth_headers(admin_user))
    assert busy.status_code == 503
    await _hold_lock(rig_a.route_id, for_=timedelta(seconds=-1))  # the holder's lease ended
    retry = await api_client.post(url, json={"reason": "x"}, headers=auth_headers(admin_user))
    assert retry.status_code == 200
    assert retry.json()["operative_status"] == "cancelled"
