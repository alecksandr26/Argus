"""Re-exports + small shared request/response shapes for the API boundary."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.geo import Coordinates  # noqa: F401  (re-exported: `from app.schemas.common import Coordinates`)


class Message(BaseModel):
    """A plain `{"detail": "..."}` body for simple confirmation responses (e.g. DELETE)."""

    detail: str


# Every "password" field on this API's boundary (login, user create/update) carries the SHA-256
# hex digest of the real password, computed client-side via the Web Crypto API
# (`crypto.subtle.digest`) before it ever leaves the browser — never the raw password. The
# backend then bcrypt-hashes *that digest* via `app.auth.security.hash_secret` exactly like it
# would any other secret; `hash_secret`/`verify_secret` themselves need no awareness of this,
# since they just hash/compare whatever string they're given. This is a defense-in-depth layer
# on top of TLS, not a replacement for it, and it has a useful side effect: `crypto.subtle` is
# only available in a secure context (HTTPS or localhost) by browser spec, so a production
# deployment served over plain HTTP simply can't compute this and login fails outright — a
# deliberate nudge toward HTTPS, not a bug to route around. The root_admin bootstrap
# (`app.auth.bootstrap.ensure_root_admin`) and `scripts/seed_dev_data.py` both reproduce this
# same `sha256-then-bcrypt` pipeline server-side from a raw env-var/literal password, so a real
# browser login with that raw password still succeeds afterward.
Sha256HexDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
