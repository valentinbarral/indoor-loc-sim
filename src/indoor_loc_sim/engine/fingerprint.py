from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial.distance import cdist

from indoor_loc_sim.core.models import Beacon, Door, Wall
from indoor_loc_sim.core.trajectory import TrajectoryPoint
from indoor_loc_sim.engine.signals import BeaconSignal, SignalType, rss_model
from indoor_loc_sim.engine.geometry import count_wall_crossings


class CancelledError(Exception):
    """Raised when a long-running operation is cancelled by the user."""


@dataclass
class FingerprintEntry:
    position: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    rss_values: np.ndarray = field(default_factory=lambda: np.array([]))

    def to_dict(self) -> dict:
        return {
            "position": self.position.tolist(),
            "rss_values": self.rss_values.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> FingerprintEntry:
        return cls(
            position=np.array(d.get("position", [0.0, 0.0, 0.0])),
            rss_values=np.array(d.get("rss_values", [])),
        )


@dataclass
class RadioMap:
    entries: list[FingerprintEntry] = field(default_factory=list)
    beacons: list[Beacon] = field(default_factory=list)
    grid_spacing: float = 1.0

    @property
    def positions(self) -> np.ndarray:
        return np.array([e.position for e in self.entries])

    @property
    def rss_matrix(self) -> np.ndarray:
        return np.array([e.rss_values for e in self.entries])

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "beacons": [b.to_dict() for b in self.beacons],
            "grid_spacing": self.grid_spacing,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RadioMap:
        return cls(
            entries=[FingerprintEntry.from_dict(e) for e in d.get("entries", [])],
            beacons=[Beacon.from_dict(b) for b in d.get("beacons", [])],
            grid_spacing=d.get("grid_spacing", 1.0),
        )


def build_radio_map(
    beacons: list[Beacon],
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    z: float = 0.0,
    grid_spacing: float = 1.0,
    n_samples: int = 10,
    rss_sigma: float = 0.1,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    path_loss_exponent: float = 2.0,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RadioMap:
    xs = np.arange(x_range[0], x_range[1] + grid_spacing / 2, grid_spacing)
    ys = np.arange(y_range[0], y_range[1] + grid_spacing / 2, grid_spacing)
    total_points = len(xs) * len(ys)

    wall_list = walls or []
    door_list = doors or []
    use_att = wall_attenuation_db > 0 and wall_list

    entries = []
    count = 0
    last_reported = 0
    for x in xs:
        for y in ys:
            if is_cancelled and is_cancelled():
                raise CancelledError()

            rss_sum = np.zeros(len(beacons))
            for _ in range(n_samples):
                point = TrajectoryPoint(x=float(x), y=float(y), z=z)
                for j, beacon in enumerate(beacons):
                    val = rss_model(
                        point,
                        beacon,
                        sigma=rss_sigma,
                        path_loss_exponent=path_loss_exponent,
                        rssi_at_ref=rssi_at_ref,
                        d0=d0,
                    )
                    if use_att:
                        n_walls = count_wall_crossings(
                            point.r, beacon.r, wall_list, door_list
                        )
                        val -= n_walls * wall_attenuation_db
                    rss_sum[j] += val
            rss_mean = rss_sum / n_samples
            entries.append(
                FingerprintEntry(
                    position=np.array([x, y, z]),
                    rss_values=rss_mean,
                )
            )
            count += 1
            report_interval = 1 if count <= 10 else _PROGRESS_YIELD_INTERVAL
            if progress_callback and (
                count - last_reported >= report_interval or count == total_points
            ):
                progress_callback(count, total_points)
                last_reported = count
                time.sleep(0)

    return RadioMap(entries=entries, beacons=beacons, grid_spacing=grid_spacing)


FINGERPRINT_METRICS: dict[str, str] = {
    "Euclidean": "euclidean",
    "Manhattan": "cityblock",
    "Cosine": "cosine",
    "Correlation": "correlation",
}

_REF_GRID_SPACING: float = 2.0
_PROGRESS_YIELD_INTERVAL: int = 10


def compute_adaptive_k(k: int, grid_spacing: float) -> int:
    """Scale *k* with (ref_spacing / grid_spacing)² to keep the spatial
    averaging area roughly constant across grid resolutions."""
    if grid_spacing <= 0 or grid_spacing >= _REF_GRID_SPACING:
        return k
    ratio = _REF_GRID_SPACING / grid_spacing
    return max(k, round(ratio * ratio * k))


@dataclass
class FingerprintResult:
    """Rich result from fingerprint k-NN estimation."""

    trajectory: list[TrajectoryPoint] = field(default_factory=list)
    neighbor_indices: list[np.ndarray] = field(default_factory=list)
    neighbor_distances: list[np.ndarray] = field(default_factory=list)
    radio_map: RadioMap | None = None

    def to_dict(self) -> dict:
        return {
            "trajectory": [p.to_dict() for p in self.trajectory],
            "neighbor_indices": [n.tolist() for n in self.neighbor_indices],
            "neighbor_distances": [d.tolist() for d in self.neighbor_distances],
            "radio_map": self.radio_map.to_dict()
            if self.radio_map is not None
            else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FingerprintResult:
        radio_map_data = d.get("radio_map")
        return cls(
            trajectory=[TrajectoryPoint.from_dict(p) for p in d.get("trajectory", [])],
            neighbor_indices=[np.array(n) for n in d.get("neighbor_indices", [])],
            neighbor_distances=[
                np.array(dist) for dist in d.get("neighbor_distances", [])
            ],
            radio_map=RadioMap.from_dict(radio_map_data) if radio_map_data else None,
        )


def estimate_fingerprint_knn(
    signal: BeaconSignal,
    radio_map: RadioMap,
    initial_state: TrajectoryPoint,
    k: int = 3,
    auto_k: bool = True,
    metric: str = "euclidean",
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> FingerprintResult:
    effective_k = compute_adaptive_k(k, radio_map.grid_spacing) if auto_k else k
    effective_k = min(effective_k, len(radio_map.entries))

    rss_matrix = radio_map.rss_matrix
    positions = radio_map.positions
    total_steps = len(signal.timeline)

    trajectory: list[TrajectoryPoint] = []
    all_neighbor_indices: list[np.ndarray] = []
    all_neighbor_distances: list[np.ndarray] = []
    last_reported = 0

    for i in range(total_steps):
        if is_cancelled and is_cancelled():
            raise CancelledError()

        t = signal.timeline[i]
        meas = signal.measurements[i]
        rss_observed = meas.values.reshape(1, -1)

        distances = cdist(rss_observed, rss_matrix, metric=metric)[0]
        nearest_indices = np.argsort(distances)[:effective_k]
        nearest_positions = positions[nearest_indices]
        nearest_distances = distances[nearest_indices]

        weights = 1.0 / (nearest_distances + 1e-10)
        weights /= weights.sum()

        estimated_pos = np.average(nearest_positions, axis=0, weights=weights)

        trajectory.append(
            TrajectoryPoint(
                x=float(estimated_pos[0]),
                y=float(estimated_pos[1]),
                z=float(initial_state.z),
                t=float(t),
            )
        )
        all_neighbor_indices.append(nearest_indices)
        all_neighbor_distances.append(nearest_distances)

        step = i + 1
        report_interval = 1 if step <= 10 else _PROGRESS_YIELD_INTERVAL
        if progress_callback and (
            step - last_reported >= report_interval or step == total_steps
        ):
            progress_callback(step, total_steps)
            last_reported = step
            time.sleep(0)

    return FingerprintResult(
        trajectory=trajectory,
        neighbor_indices=all_neighbor_indices,
        neighbor_distances=all_neighbor_distances,
        radio_map=radio_map,
    )
