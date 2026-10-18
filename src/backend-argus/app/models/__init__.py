"""Beanie Document definitions — the internal MongoDB storage shape.

These are intentionally not what the API returns or accepts directly (see `app/schemas/` for
that) — Documents use Beanie's default `id: PydanticObjectId` rather than a hand-rolled
`id_truck`-style primary key, and store coordinates as GeoJSON rather than `{lat, lon}`.
`app/serializers.py` is where a Document becomes a response schema.
"""
from .alert import Alert
from .driver import Driver
from .route import Route
from .simulator_session import SimulatorSession
from .status_route import StatusRoute
from .truck import Truck
from .user import User

# Beanie's init_beanie(document_models=...) call in app/database.py needs this list.
DOCUMENT_MODELS = [User, Truck, Driver, Route, StatusRoute, Alert, SimulatorSession]

__all__ = [
    "Alert",
    "Driver",
    "Route",
    "SimulatorSession",
    "StatusRoute",
    "Truck",
    "User",
    "DOCUMENT_MODELS",
]
