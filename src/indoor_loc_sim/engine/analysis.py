from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import interp1d

from indoor_loc_sim.core.trajectory import TrajectoryPoint


@dataclass
class ErrorAnalysis:
    times: np.ndarray = field(default_factory=lambda: np.array([]))
    errors: np.ndarray = field(default_factory=lambda: np.array([]))
    error_x: np.ndarray = field(default_factory=lambda: np.array([]))
    error_y: np.ndarray = field(default_factory=lambda: np.array([]))
    label: str = ""

    @property
    def mean_error(self) -> float:
        return float(np.mean(self.errors)) if len(self.errors) > 0 else 0.0

    @property
    def max_error(self) -> float:
        return float(np.max(self.errors)) if len(self.errors) > 0 else 0.0

    @property
    def percentile_90(self) -> float:
        return float(np.percentile(self.errors, 90)) if len(self.errors) > 0 else 0.0

    @property
    def percentile_50(self) -> float:
        return float(np.percentile(self.errors, 50)) if len(self.errors) > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "times": self.times.tolist(),
            "errors": self.errors.tolist(),
            "error_x": self.error_x.tolist(),
            "error_y": self.error_y.tolist(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ErrorAnalysis:
        return cls(
            times=np.array(d.get("times", [])),
            errors=np.array(d.get("errors", [])),
            error_x=np.array(d.get("error_x", [])),
            error_y=np.array(d.get("error_y", [])),
            label=d.get("label", ""),
        )

    def cdf(self) -> tuple[np.ndarray, np.ndarray]:
        if len(self.errors) == 0:
            return np.array([]), np.array([])
        sorted_errors = np.sort(self.errors)
        cdf_values = np.arange(1, len(sorted_errors) + 1) / len(sorted_errors)
        return sorted_errors, cdf_values


def compute_errors(
    real_trajectory: list[TrajectoryPoint],
    estimated_trajectory: list[TrajectoryPoint],
    label: str = "",
) -> ErrorAnalysis:
    if not real_trajectory or not estimated_trajectory:
        return ErrorAnalysis(label=label)

    # Use the estimated trajectory timestamps as reference and interpolate
    # the ground truth to those same instants.  The previous implementation
    # compared by *index* which silently produced enormous fictitious errors
    # whenever the ground-truth and estimation had different sampling rates.
    est_times = np.array([p.t for p in estimated_trajectory])
    est_x = np.array([p.x for p in estimated_trajectory])
    est_y = np.array([p.y for p in estimated_trajectory])

    gt_times = np.array([p.t for p in real_trajectory])
    gt_x = np.array([p.x for p in real_trajectory])
    gt_y = np.array([p.y for p in real_trajectory])

    t_min = max(gt_times[0], est_times[0])
    t_max = min(gt_times[-1], est_times[-1])
    mask = (est_times >= t_min - 1e-9) & (est_times <= t_max + 1e-9)
    if not np.any(mask):
        return ErrorAnalysis(label=label)

    eval_times = est_times[mask]
    eval_est_x = est_x[mask]
    eval_est_y = est_y[mask]

    interp_gt_x = interp1d(gt_times, gt_x, kind="linear", fill_value="extrapolate")
    interp_gt_y = interp1d(gt_times, gt_y, kind="linear", fill_value="extrapolate")

    gt_x_at_t = interp_gt_x(eval_times)
    gt_y_at_t = interp_gt_y(eval_times)

    error_x = eval_est_x - gt_x_at_t
    error_y = eval_est_y - gt_y_at_t
    errors = np.sqrt(error_x**2 + error_y**2)

    return ErrorAnalysis(
        times=eval_times,
        errors=errors,
        error_x=error_x,
        error_y=error_y,
        label=label,
    )
