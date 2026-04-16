from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import interp1d


@dataclass
class TrajectoryPoint:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    t: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0

    @property
    def r(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    @property
    def v(self) -> np.ndarray:
        return np.array([self.vx, self.vy, self.vz])

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "t": self.t,
            "vx": self.vx,
            "vy": self.vy,
            "vz": self.vz,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrajectoryPoint:
        return cls(
            x=d.get("x", 0.0),
            y=d.get("y", 0.0),
            z=d.get("z", 0.0),
            t=d.get("t", 0.0),
            vx=d.get("vx", 0.0),
            vy=d.get("vy", 0.0),
            vz=d.get("vz", 0.0),
        )


@dataclass
class Segment:
    points: list[TrajectoryPoint] = field(default_factory=list)
    segment_type: str = "byFloor"
    _cumsum: np.ndarray | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if len(self.points) > 1:
            self._compute_cumsum()

    def _compute_cumsum(self) -> None:
        dists = [0.0]
        for i in range(1, len(self.points)):
            d = np.linalg.norm(self.points[i].r - self.points[i - 1].r)
            dists.append(d)
        self._cumsum = np.cumsum(dists)

    @property
    def cumulative_distances(self) -> np.ndarray:
        if self._cumsum is None:
            self._compute_cumsum()
        return self._cumsum

    @property
    def total_length(self) -> float:
        return float(self.cumulative_distances[-1]) if len(self.points) > 1 else 0.0

    def resample(self, dx: float = 0.5) -> Segment:
        if len(self.points) < 2:
            return Segment(points=list(self.points), segment_type=self.segment_type)

        cs = self.cumulative_distances
        new_cs = np.arange(0, cs[-1], dx)
        if new_cs[-1] < cs[-1]:
            new_cs = np.append(new_cs, cs[-1])

        xs = np.array([p.x for p in self.points])
        ys = np.array([p.y for p in self.points])
        zs = np.array([p.z for p in self.points])

        new_x = np.interp(new_cs, cs, xs)
        new_y = np.interp(new_cs, cs, ys)
        new_z = np.interp(new_cs, cs, zs)

        new_points = [
            TrajectoryPoint(x=float(new_x[i]), y=float(new_y[i]), z=float(new_z[i]))
            for i in range(len(new_cs))
        ]
        return Segment(points=new_points, segment_type=self.segment_type)


def _apply_velocity_model(
    segment: Segment, walking_speed: float = 1.2
) -> list[TrajectoryPoint]:
    if len(segment.points) < 2:
        if segment.points:
            p = segment.points[0]
            return [TrajectoryPoint(x=p.x, y=p.y, z=p.z, t=0.0)]
        return []

    cs = segment.cumulative_distances
    times = cs / walking_speed

    xs = np.array([p.x for p in segment.points])
    ys = np.array([p.y for p in segment.points])
    zs = np.array([p.z for p in segment.points])

    # Velocity via finite differences
    dt = np.diff(times)
    dt[dt == 0] = 1e-10
    vx = np.zeros_like(xs)
    vy = np.zeros_like(ys)
    vz = np.zeros_like(zs)

    vx[:-1] = np.diff(xs) / dt
    vy[:-1] = np.diff(ys) / dt
    vz[:-1] = np.diff(zs) / dt

    vx[-1] = vx[-2] if len(vx) > 1 else 0.0
    vy[-1] = vy[-2] if len(vy) > 1 else 0.0
    vz[-1] = vz[-2] if len(vz) > 1 else 0.0

    result = []
    for i in range(len(xs)):
        result.append(
            TrajectoryPoint(
                x=float(xs[i]),
                y=float(ys[i]),
                z=float(zs[i]),
                t=float(times[i]),
                vx=float(vx[i]),
                vy=float(vy[i]),
                vz=float(vz[i]),
            )
        )
    return result


@dataclass
class GroundTruth:
    events: list[TrajectoryPoint] = field(default_factory=list)
    frequency: float = 5.0
    label: str = "GroundTruth"

    def to_dict(self) -> dict:
        return {
            "events": [e.to_dict() for e in self.events],
            "frequency": self.frequency,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GroundTruth:
        return cls(
            events=[TrajectoryPoint.from_dict(e) for e in d.get("events", [])],
            frequency=d.get("frequency", 5.0),
            label=d.get("label", "GroundTruth"),
        )

    @property
    def timeline(self) -> np.ndarray:
        if not self.events:
            return np.array([])
        t_end = self.events[-1].t
        return np.arange(0, t_end + 1e-10, 1.0 / self.frequency)

    def step(self, t: float | np.ndarray) -> list[TrajectoryPoint]:
        if not self.events:
            return []

        times = np.array([e.t for e in self.events])
        xs = np.array([e.x for e in self.events])
        ys = np.array([e.y for e in self.events])
        zs = np.array([e.z for e in self.events])
        vxs = np.array([e.vx for e in self.events])
        vys = np.array([e.vy for e in self.events])
        vzs = np.array([e.vz for e in self.events])

        t_arr = np.atleast_1d(t)

        interp_x = interp1d(times, xs, kind="linear", fill_value="extrapolate")
        interp_y = interp1d(times, ys, kind="linear", fill_value="extrapolate")
        interp_z = interp1d(times, zs, kind="nearest", fill_value="extrapolate")
        interp_vx = interp1d(times, vxs, kind="linear", fill_value="extrapolate")
        interp_vy = interp1d(times, vys, kind="linear", fill_value="extrapolate")
        interp_vz = interp1d(times, vzs, kind="linear", fill_value="extrapolate")

        result = []
        for ti in t_arr:
            result.append(
                TrajectoryPoint(
                    x=float(interp_x(ti)),
                    y=float(interp_y(ti)),
                    z=float(interp_z(ti)),
                    t=float(ti),
                    vx=float(interp_vx(ti)),
                    vy=float(interp_vy(ti)),
                    vz=float(interp_vz(ti)),
                )
            )
        return result


def generate_ground_truth(
    waypoints: list[tuple[float, float, float]],
    frequency: float = 5.0,
    walking_speed: float = 1.2,
) -> GroundTruth:
    if len(waypoints) < 2:
        events = [TrajectoryPoint(x=w[0], y=w[1], z=w[2]) for w in waypoints]
        return GroundTruth(events=events, frequency=frequency)

    points = [TrajectoryPoint(x=w[0], y=w[1], z=w[2]) for w in waypoints]
    seg = Segment(points=points, segment_type="byFloor")
    resample_dx = max(walking_speed / max(frequency, 1e-10), 1e-3)
    resampled = seg.resample(dx=resample_dx)
    events_with_velocity = _apply_velocity_model(resampled, walking_speed)

    gt = GroundTruth(events=events_with_velocity, frequency=frequency)

    tl = gt.timeline
    if len(tl) > 0:
        resampled_events = gt.step(tl)
        gt = GroundTruth(events=resampled_events, frequency=frequency)

    return gt
