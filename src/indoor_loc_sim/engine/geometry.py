from __future__ import annotations

import numpy as np

from indoor_loc_sim.core.models import Door, Wall


def _segments_intersect_batch(
    p1: np.ndarray,
    p2: np.ndarray,
    wall_starts: np.ndarray,
    wall_ends: np.ndarray,
) -> np.ndarray:
    """Vectorized test: does segment p1→p2 intersect each wall segment?

    Uses the parametric intersection method.
    Returns boolean array of shape (n_walls,).
    """
    d = p2 - p1
    e = wall_ends - wall_starts

    denom = d[0] * e[:, 1] - d[1] * e[:, 0]

    f = wall_starts - p1
    t = f[:, 0] * e[:, 1] - f[:, 1] * e[:, 0]
    u = f[:, 0] * d[1] - f[:, 1] * d[0]

    parallel = np.abs(denom) < 1e-12
    safe_denom = np.where(parallel, 1.0, denom)
    t_param = t / safe_denom
    u_param = u / safe_denom

    eps = 1e-9
    hit = (
        (~parallel)
        & (t_param > eps)
        & (t_param < 1.0 - eps)
        & (u_param > eps)
        & (u_param < 1.0 - eps)
    )
    return hit


def _walls_to_arrays(walls: list[Wall]) -> tuple[np.ndarray, np.ndarray]:
    if not walls:
        return np.empty((0, 2)), np.empty((0, 2))
    starts = np.array([[w.start.x, w.start.y] for w in walls])
    ends = np.array([[w.end.x, w.end.y] for w in walls])
    return starts, ends


def count_wall_crossings(
    point: np.ndarray,
    beacon: np.ndarray,
    walls: list[Wall],
    doors: list[Door] | None = None,
) -> int:
    if not walls:
        return 0

    wall_starts, wall_ends = _walls_to_arrays(walls)
    p1 = point[:2]
    p2 = beacon[:2]

    hits = _segments_intersect_batch(p1, p2, wall_starts, wall_ends)
    count = int(np.sum(hits))

    if doors:
        door_starts, door_ends = _walls_to_arrays(doors)  # type: ignore[arg-type]
        door_hits = _segments_intersect_batch(p1, p2, door_starts, door_ends)
        count -= int(np.sum(door_hits))
        count = max(count, 0)

    return count


def has_line_of_sight(
    point: np.ndarray,
    beacon: np.ndarray,
    walls: list[Wall],
    doors: list[Door] | None = None,
) -> bool:
    return count_wall_crossings(point, beacon, walls, doors) == 0
