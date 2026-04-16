from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from indoor_loc_sim.core.models import Building
from indoor_loc_sim.core.trajectory import GroundTruth, TrajectoryPoint
from indoor_loc_sim.engine.analysis import ErrorAnalysis
from indoor_loc_sim.engine.fingerprint import FingerprintResult
from indoor_loc_sim.engine.signals import BeaconSignal


RUN_COLORS = [
    QColor("#e74c3c"),
    QColor("#2ecc71"),
    QColor("#f39c12"),
    QColor("#9b59b6"),
    QColor("#1abc9c"),
    QColor("#e67e22"),
    QColor("#3498db"),
    QColor("#34495e"),
]


@dataclass
class SimulationRun:
    run_id: str
    algorithm: str
    display_label: str
    params: dict[str, float | int | str]
    trajectory: list[TrajectoryPoint]
    analysis: ErrorAnalysis
    color: QColor = field(default_factory=lambda: QColor("#e74c3c"))
    visible: bool = True
    fingerprint_result: FingerprintResult | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "algorithm": self.algorithm,
            "display_label": self.display_label,
            "params": self.params,
            "trajectory": [p.to_dict() for p in self.trajectory],
            "analysis": self.analysis.to_dict(),
            "color": self.color.name(),
            "visible": self.visible,
            "fingerprint_result": (
                self.fingerprint_result.to_dict()
                if self.fingerprint_result is not None
                else None
            ),
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SimulationRun:
        fp_result_data = d.get("fingerprint_result")
        timestamp_raw = d.get("timestamp")
        return cls(
            run_id=d.get("run_id", ""),
            algorithm=d.get("algorithm", ""),
            display_label=d.get("display_label", ""),
            params=d.get("params", {}),
            trajectory=[TrajectoryPoint.from_dict(p) for p in d.get("trajectory", [])],
            analysis=ErrorAnalysis.from_dict(d.get("analysis", {})),
            color=QColor(d.get("color", "#e74c3c")),
            visible=d.get("visible", True),
            fingerprint_result=(
                FingerprintResult.from_dict(fp_result_data)
                if fp_result_data is not None
                else None
            ),
            timestamp=(
                datetime.fromisoformat(timestamp_raw)
                if timestamp_raw
                else datetime.now()
            ),
        )


class AppState(QObject):
    building_changed = Signal()
    trajectory_changed = Signal()
    signals_changed = Signal()
    estimation_changed = Signal()
    analysis_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.building: Building = Building()
        self.current_level_index: int = 0

        self.waypoints: list[tuple[float, float, float]] = []
        self.ground_truth: GroundTruth | None = None

        self.beacon_signals: list[BeaconSignal] = []
        self.simulation_runs: list[SimulationRun] = []
        self._run_counters: dict[str, int] = {}
        self._color_counter: int = 0

        self.signal_tab_params: dict[str, float] = {
            "rss_sigma": 2.0,
            "path_loss_exponent": 2.0,
            "wall_attenuation_db": 3.0,
            "rssi_at_ref": -59.0,
            "d0": 1.0,
        }

    def set_building(self, building: Building) -> None:
        self.building = building
        if building.levels:
            self.current_level_index = 0
        self.building_changed.emit()

    def set_ground_truth(self, gt: GroundTruth) -> None:
        self.ground_truth = gt
        self.trajectory_changed.emit()

    def set_waypoints_and_ground_truth(
        self,
        waypoints: list[tuple[float, float, float]],
        ground_truth: GroundTruth | None,
    ) -> None:
        self.waypoints = list(waypoints)
        self.ground_truth = ground_truth
        self.trajectory_changed.emit()

    def clear_trajectory(self) -> None:
        self.waypoints.clear()
        self.ground_truth = None
        self.trajectory_changed.emit()

    def set_beacon_signals(self, signals: list[BeaconSignal]) -> None:
        self.beacon_signals = signals
        self.signals_changed.emit()

    def _rebuild_run_counters(self) -> None:
        self._run_counters.clear()
        for run in self.simulation_runs:
            prefix = f"{run.algorithm}_"
            count = self._run_counters.get(run.algorithm, 0)
            if run.run_id.startswith(prefix):
                suffix = run.run_id[len(prefix) :]
                if suffix.isdigit():
                    count = max(count, int(suffix))
                else:
                    count += 1
            else:
                count += 1
            self._run_counters[run.algorithm] = count
        self._color_counter = len(self.simulation_runs)

    def next_run_id(self, algorithm: str) -> tuple[str, str, QColor]:
        count = self._run_counters.get(algorithm, 0) + 1
        self._run_counters[algorithm] = count
        short_name = algorithm.replace("pos2D_", "")
        run_id = f"{algorithm}_{count:03d}"
        display_label = f"{short_name} #{count}"
        color = RUN_COLORS[self._color_counter % len(RUN_COLORS)]
        self._color_counter += 1
        return run_id, display_label, color

    def add_simulation_run(self, run: SimulationRun) -> None:
        self.simulation_runs.append(run)
        self._rebuild_run_counters()
        self.estimation_changed.emit()
        self.analysis_changed.emit()

    def set_simulation_runs(self, runs: list[SimulationRun]) -> None:
        self.simulation_runs = list(runs)
        self._rebuild_run_counters()
        self.estimation_changed.emit()
        self.analysis_changed.emit()

    def remove_simulation_run(self, run_id: str) -> None:
        self.simulation_runs = [r for r in self.simulation_runs if r.run_id != run_id]
        self._rebuild_run_counters()
        self.estimation_changed.emit()
        self.analysis_changed.emit()

    def clear_estimations(self) -> None:
        self.simulation_runs.clear()
        self._run_counters.clear()
        self._color_counter = 0
        self.estimation_changed.emit()
        self.analysis_changed.emit()

    def visible_runs(self) -> list[SimulationRun]:
        return [r for r in self.simulation_runs if r.visible]

    def fingerprint_runs(self) -> list[SimulationRun]:
        return [r for r in self.simulation_runs if r.fingerprint_result is not None]
