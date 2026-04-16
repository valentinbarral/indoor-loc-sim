from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from indoor_loc_sim.core.models import Beacon, Door, Wall
from indoor_loc_sim.core.trajectory import GroundTruth, TrajectoryPoint
from indoor_loc_sim.engine.geometry import count_wall_crossings, has_line_of_sight


class SignalType(Enum):
    RSS = "RSS"
    TOF = "ToF"
    AOA = "AoA"


class NlosMode(Enum):
    NONE = "none"
    INCREASE_ERROR = "increase_error"
    SKIP = "skip"


SPEED_OF_LIGHT = 3e8


def rss_model(
    position: TrajectoryPoint,
    beacon: Beacon,
    sigma: float = 2.0,
    path_loss_exponent: float = 2.0,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
) -> float:
    """Log-distance path-loss: RSSI = A − 10·n·log10(d/d0) + N(0, σ)."""
    r = position.r
    rb = beacon.r
    distance = float(np.linalg.norm(rb - r))
    distance = max(distance, 0.1)  # clamp to 10 cm
    return float(
        rssi_at_ref
        - 10.0 * path_loss_exponent * np.log10(distance / d0)
        + np.random.normal(0, sigma)
    )


def tof_model(position: TrajectoryPoint, beacon: Beacon, sigma: float = 1e-9) -> float:
    # ToF = distance / c + N(0, sigma)
    r = position.r
    rb = beacon.r
    distance = np.linalg.norm(rb - r)
    return float(distance / SPEED_OF_LIGHT + np.random.normal(0, sigma))


def aoa_model(position: TrajectoryPoint, beacon: Beacon) -> float:
    v = position.v
    rb = beacon.r
    v_norm = np.linalg.norm(v)
    rb_norm = np.linalg.norm(rb)
    if v_norm < 1e-10 or rb_norm < 1e-10:
        return 0.0
    return float(np.arccos(np.clip(np.dot(v, rb) / (v_norm * rb_norm), -1.0, 1.0)))


SIGNAL_MODELS = {
    SignalType.RSS: rss_model,
    SignalType.TOF: tof_model,
    SignalType.AOA: aoa_model,
}


@dataclass
class SignalMeasurement:
    values: np.ndarray = field(default_factory=lambda: np.array([]))
    beacon_indices: list[int] = field(default_factory=list)
    position: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0

    def to_dict(self) -> dict:
        return {
            "values": self.values.tolist(),
            "beacon_indices": list(self.beacon_indices),
            "position": self.position.tolist(),
            "vx": self.vx,
            "vy": self.vy,
            "vz": self.vz,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SignalMeasurement:
        return cls(
            values=np.array(d.get("values", [])),
            beacon_indices=list(d.get("beacon_indices", [])),
            position=np.array(d.get("position", [0.0, 0.0, 0.0])),
            vx=d.get("vx", 0.0),
            vy=d.get("vy", 0.0),
            vz=d.get("vz", 0.0),
        )


@dataclass
class BeaconSignal:
    signal_type: SignalType = SignalType.RSS
    timeline: np.ndarray = field(default_factory=lambda: np.array([]))
    measurements: list[SignalMeasurement] = field(default_factory=list)
    beacons: list[Beacon] = field(default_factory=list)
    frequency: float = 5.0
    label: str = ""

    @property
    def n_beacons(self) -> int:
        return len(self.beacons)

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type.value,
            "timeline": self.timeline.tolist(),
            "measurements": [m.to_dict() for m in self.measurements],
            "beacons": [b.to_dict() for b in self.beacons],
            "frequency": self.frequency,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BeaconSignal:
        return cls(
            signal_type=SignalType(d.get("signal_type", SignalType.RSS.value)),
            timeline=np.array(d.get("timeline", [])),
            measurements=[
                SignalMeasurement.from_dict(m) for m in d.get("measurements", [])
            ],
            beacons=[Beacon.from_dict(b) for b in d.get("beacons", [])],
            frequency=d.get("frequency", 5.0),
            label=d.get("label", ""),
        )

    def values_for_beacon(self, beacon_index: int) -> np.ndarray:
        return np.array([m.values[beacon_index] for m in self.measurements])

    def step(self, t: float) -> SignalMeasurement:
        idx = int(np.searchsorted(self.timeline, t, side="right")) - 1
        idx = max(0, min(idx, len(self.measurements) - 1))
        return self.measurements[idx]


def _measure_one_point(
    pos: TrajectoryPoint,
    beacons: list[Beacon],
    signal_type: SignalType,
    rss_sigma: float,
    tof_sigma: float,
    path_loss_exponent: float,
    wall_list: list[Wall],
    door_list: list[Door],
    wall_attenuation_db: float,
    nlos_mode: NlosMode,
    nlos_error_multiplier: float,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
) -> np.ndarray:
    use_wall_att = wall_attenuation_db > 0 and wall_list
    use_nlos = nlos_mode != NlosMode.NONE and wall_list
    values = np.zeros(len(beacons))

    for j, beacon in enumerate(beacons):
        if signal_type == SignalType.RSS:
            val = rss_model(
                pos,
                beacon,
                sigma=rss_sigma,
                path_loss_exponent=path_loss_exponent,
                rssi_at_ref=rssi_at_ref,
                d0=d0,
            )
            if use_wall_att:
                n_walls = count_wall_crossings(pos.r, beacon.r, wall_list, door_list)
                val -= n_walls * wall_attenuation_db
            values[j] = val

        elif signal_type == SignalType.TOF:
            if use_nlos:
                los = has_line_of_sight(pos.r, beacon.r, wall_list, door_list)
                if not los:
                    if nlos_mode == NlosMode.SKIP:
                        values[j] = float("nan")
                        continue
                    elif nlos_mode == NlosMode.INCREASE_ERROR:
                        values[j] = tof_model(
                            pos,
                            beacon,
                            sigma=tof_sigma * nlos_error_multiplier,
                        )
                        continue
            values[j] = tof_model(pos, beacon, sigma=tof_sigma)

        else:
            values[j] = aoa_model(pos, beacon)

    return values


def generate_beacon_signal(
    ground_truth: GroundTruth,
    beacons: list[Beacon],
    signal_type: SignalType = SignalType.RSS,
    rss_sigma: float = 2.0,
    tof_sigma: float = 1e-9,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    nlos_mode: NlosMode = NlosMode.NONE,
    nlos_error_multiplier: float = 10.0,
    path_loss_exponent: float = 2.0,
    n_samples: int = 1,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
) -> BeaconSignal:
    if not beacons or not ground_truth.events:
        return BeaconSignal(
            signal_type=signal_type,
            beacons=beacons,
            frequency=ground_truth.frequency,
        )

    positions = ground_truth.events
    timeline = np.array([p.t for p in positions])

    wall_list = walls or []
    door_list = doors or []

    measurements = []
    for pos in positions:
        if n_samples <= 1:
            values = _measure_one_point(
                pos,
                beacons,
                signal_type,
                rss_sigma,
                tof_sigma,
                path_loss_exponent,
                wall_list,
                door_list,
                wall_attenuation_db,
                nlos_mode,
                nlos_error_multiplier,
                rssi_at_ref,
                d0,
            )
        else:
            acc = np.zeros(len(beacons))
            for _ in range(n_samples):
                acc += _measure_one_point(
                    pos,
                    beacons,
                    signal_type,
                    rss_sigma,
                    tof_sigma,
                    path_loss_exponent,
                    wall_list,
                    door_list,
                    wall_attenuation_db,
                    nlos_mode,
                    nlos_error_multiplier,
                    rssi_at_ref,
                    d0,
                )
            values = acc / n_samples

        measurements.append(
            SignalMeasurement(
                values=values,
                beacon_indices=list(range(len(beacons))),
                position=pos.r,
                vx=pos.vx,
                vy=pos.vy,
                vz=pos.vz,
            )
        )

    return BeaconSignal(
        signal_type=signal_type,
        timeline=timeline,
        measurements=measurements,
        beacons=beacons,
        frequency=ground_truth.frequency,
    )


@dataclass
class HeatmapResult:
    """2-D RSS power grid for visualisation."""

    grid: np.ndarray  # shape (ny, nx)
    x_edges: np.ndarray  # shape (nx,)
    y_edges: np.ndarray  # shape (ny,)
    beacon_index: int | None  # None ⇒ averaged over all beacons


def generate_rss_heatmap(
    beacons: list[Beacon],
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    z: float = 0.0,
    resolution: float = 0.5,
    rss_sigma: float = 0.0,
    walls: list[Wall] | None = None,
    doors: list[Door] | None = None,
    wall_attenuation_db: float = 0.0,
    beacon_index: int | None = None,
    path_loss_exponent: float = 2.0,
    rssi_at_ref: float = -59.0,
    d0: float = 1.0,
) -> HeatmapResult:
    """Compute an RSS power grid.  *beacon_index=None* → mean over all beacons."""
    xs = np.arange(x_range[0], x_range[1] + resolution / 2, resolution)
    ys = np.arange(y_range[0], y_range[1] + resolution / 2, resolution)

    wall_list = walls or []
    door_list = doors or []
    use_att = wall_attenuation_db > 0 and len(wall_list) > 0

    target_beacons = [beacons[beacon_index]] if beacon_index is not None else beacons

    grid = np.zeros((len(ys), len(xs)))

    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            point = TrajectoryPoint(x=float(x), y=float(y), z=z)
            total = 0.0
            for beacon in target_beacons:
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
                total += val
            grid[iy, ix] = total / len(target_beacons)

    return HeatmapResult(
        grid=grid,
        x_edges=xs,
        y_edges=ys,
        beacon_index=beacon_index,
    )
