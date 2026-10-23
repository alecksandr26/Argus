"""GPS track helpers for `GET /api/live` and `GET /api/routes/{id}/track` (C3.6)."""
from __future__ import annotations

from typing import Sequence, TypeVar

from app.models.common import Vigilance

T = TypeVar("T")


def downsample(points: Sequence[T], max_points: int, *, vigilance=lambda p: p.vigilance) -> list[T]:
    """At most `max_points` of `points` (already in time order), keeping the first and the last,
    then every point whose vigilance isn't `low` (newest first if even those don't fit), then an
    even stride over the rest. The result stays in time order."""
    n = len(points)
    if n <= max_points:
        return list(points)
    if max_points <= 2:
        return [points[0], points[-1]][:max_points] if max_points > 0 else []
    keep: set[int] = {0, n - 1}
    notable = [i for i in range(1, n - 1) if vigilance(points[i]) != Vigilance.LOW]
    room = max_points - len(keep)
    if len(notable) >= room:
        keep.update(notable[-room:])
    else:
        keep.update(notable)
        rest = [i for i in range(1, n - 1) if i not in keep]
        room = max_points - len(keep)
        if room > 0 and rest:
            stride = len(rest) / room
            keep.update(rest[int(k * stride)] for k in range(room))
    return [points[i] for i in sorted(keep)]
