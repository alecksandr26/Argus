"""`scripts/seed_dev_data.py` — verifies the idempotent (`reset=False`) path actually is
idempotent (no duplicates on a second run, the shape `SEED_DEMO_DATA` relies on for every
backend startup) and that the destructive (`reset=True`) path still seeds the full dataset."""
import pytest

from app.guardians import is_usable_guardian
from app.models.alert import Alert
from app.models.common import Role
from app.models.driver import Driver
from app.models.incident import Incident
from app.models.route import Route
from app.models.truck import Truck
from app.models.user import User
from scripts.seed_dev_data import DEMO_PASSWORD, seed


@pytest.mark.asyncio
async def test_idempotent_seed_does_not_duplicate_on_rerun(mongo_client):
    await seed(reset=False, client=mongo_client)
    counts_first = {
        "users": await User.find_all().count(),
        "trucks": await Truck.find_all().count(),
        "drivers": await Driver.find_all().count(),
        "routes": await Route.find_all().count(),
    }
    assert counts_first["users"] == 7
    assert counts_first["trucks"] == 5
    assert counts_first["drivers"] == 5
    assert counts_first["routes"] == 4

    await seed(reset=False, client=mongo_client)
    counts_second = {
        "users": await User.find_all().count(),
        "trucks": await Truck.find_all().count(),
        "drivers": await Driver.find_all().count(),
        "routes": await Route.find_all().count(),
    }
    assert counts_second == counts_first


@pytest.mark.asyncio
async def test_reset_seed_produces_the_full_dataset(mongo_client):
    await seed(reset=True, client=mongo_client)
    assert await User.find_all().count() == 7
    assert await Truck.find_all().count() == 5
    assert await Driver.find_all().count() == 5
    assert await Route.find_all().count() == 4
    roles = {u.role.value for u in await User.find_all().to_list()}
    assert roles == {"root_admin", "admin", "guardian"}


@pytest.mark.asyncio
async def test_seed_has_incidents_and_no_panic(mongo_client):
    """The panic button is retired, so the demo has no panic alert; every demo alert is in an
    incident (the reviewed one on the completed route closed), and the routes' incident_state
    follows their open incidents."""
    await seed(reset=True, client=mongo_client)
    alerts = await Alert.find_all().to_list()
    assert {a.source.value for a in alerts} == {"fusion"}
    assert all(a.id_incident for a in alerts)
    incidents = await Incident.find_all().to_list()
    assert len(incidents) == 3
    assert sum(i.closed_at is not None for i in incidents) == 1
    states = {r.operative_status.value: r.incident_state.value for r in await Route.find_all().to_list()}
    assert states["in_progress"] == "critical"
    assert states["in_progress_alert"] == "critical"
    assert states["completed"] == "fine"
    assert any(a.delivery.value == "dlq" and a.location_is_last_known for a in alerts)


@pytest.mark.asyncio
async def test_credentials_file_lists_every_seeded_user(mongo_client, tmp_path):
    creds_path = tmp_path / "seed_credentials.txt"
    await seed(reset=True, client=mongo_client, credentials_file=str(creds_path))

    content = creds_path.read_text(encoding="utf-8")
    assert content.count(DEMO_PASSWORD) == 7
    assert "root_admin" in content
    assert "admin@argus.dev" in content
    assert "guardian3@argus.dev" in content


@pytest.mark.asyncio
async def test_no_credentials_file_written_when_not_requested(mongo_client, tmp_path):
    await seed(reset=True, client=mongo_client)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_seeded_routes_have_guardians_with_e164_phones(mongo_client):
    """Route guardians (route-guardian contracts G1/G2): every demo route has an active guardian
    with an E.164 phone, spread over the three seeded guardians."""
    await seed(reset=True, client=mongo_client)
    routes = await Route.find_all().to_list()
    guardian_ids = {r.id_guardian for r in routes}
    assert None not in guardian_ids and len(guardian_ids) == 3
    for gid in guardian_ids:
        assert is_usable_guardian(await User.get(gid))


@pytest.mark.asyncio
async def test_idempotent_seed_fixes_an_old_guardian_phone(mongo_client):
    await User(
        email="guardian@argus.dev",
        password_hash="x",
        role=Role.GUARDIAN,
        first_name="Luis",
        last_name="Mendoza",
        phone_number="555-0101",
    ).insert()
    await seed(reset=False, client=mongo_client)
    assert (await User.find_one(User.email == "guardian@argus.dev")).phone_number == "+525550100101"
