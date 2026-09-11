"""Re-exports + small shared request/response shapes for the API boundary."""
from __future__ import annotations

from pydantic import BaseModel

from app.geo import Coordinates  # noqa: F401  (re-exported: `from app.schemas.common import Coordinates`)


class Message(BaseModel):
    """A plain `{"detail": "..."}` body for simple confirmation responses (e.g. DELETE)."""

    detail: str
