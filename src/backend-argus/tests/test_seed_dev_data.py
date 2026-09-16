"""`scripts/seed_dev_data.py` — verifies the idempotent (`reset=False`) path actually is
idempotent (no duplicates on a second run, the shape `SEED_DEMO_DATA` relies on for every
backend startup) and that the destructive (`reset=True`) path still seeds the full dataset."""
import pytest

from app.models.driver import Driver
from app.models.route import Route
from app.models.truck import Truck
from app.models.user import User
from scripts.seed_dev_data import seed


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
