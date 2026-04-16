from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from indoor_loc_sim.core.models import Building, Level
from indoor_loc_sim.core.trajectory import GroundTruth
from indoor_loc_sim.engine.signals import BeaconSignal
from indoor_loc_sim.gui.state import SimulationRun

PROJECT_EXTENSION = ".ilsim"
LEGACY_EXTENSION = ".ilsproj"
_ZIP_MAGIC = b"PK"


def save_project(
    path: str | Path,
    building: Building,
    waypoints: list[tuple[float, float, float]],
    ground_truth: GroundTruth | None = None,
    beacon_signals: list[BeaconSignal] | None = None,
    simulation_runs: list[SimulationRun] | None = None,
) -> None:
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("building.json", json.dumps(building.to_dict(), indent=2))
        zf.writestr(
            "waypoints.json", json.dumps([list(w) for w in waypoints], indent=2)
        )
        if ground_truth is not None:
            zf.writestr(
                "ground_truth.json", json.dumps(ground_truth.to_dict(), indent=2)
            )
        if beacon_signals is not None:
            zf.writestr(
                "beacon_signals.json",
                json.dumps([s.to_dict() for s in beacon_signals], indent=2),
            )
        if simulation_runs is not None:
            zf.writestr(
                "simulation_runs.json",
                json.dumps([r.to_dict() for r in simulation_runs], indent=2),
            )

        for level in building.levels:
            if not level.floor_plan_path:
                continue
            img_path = Path(level.floor_plan_path)
            if not img_path.is_file():
                continue
            archive_name = f"images/level_{level.n}{img_path.suffix}"
            zf.write(img_path, archive_name)


def load_project(
    path: str | Path,
) -> tuple[
    Building,
    list[tuple[float, float, float]],
    Path | None,
    GroundTruth | None,
    list[BeaconSignal],
    list[SimulationRun],
]:
    path = Path(path)

    with open(path, "rb") as f:
        magic = f.read(2)

    if magic != _ZIP_MAGIC:
        return _load_legacy_json(path)

    return _load_zip(path)


def _load_legacy_json(
    path: Path,
) -> tuple[
    Building,
    list[tuple[float, float, float]],
    Path | None,
    GroundTruth | None,
    list[BeaconSignal],
    list[SimulationRun],
]:
    with open(path) as f:
        data = json.load(f)
    building = Building.from_dict(data.get("building", {}))
    waypoints = [tuple(w) for w in data.get("waypoints", [])]
    return building, waypoints, None, None, [], []


def _load_zip(
    path: Path,
) -> tuple[
    Building,
    list[tuple[float, float, float]],
    Path | None,
    GroundTruth | None,
    list[BeaconSignal],
    list[SimulationRun],
]:
    temp_dir = Path(tempfile.mkdtemp(prefix="ilsim_"))

    with zipfile.ZipFile(path, "r") as zf:
        building_json = json.loads(zf.read("building.json"))
        building = Building.from_dict(building_json)

        try:
            waypoints_raw = json.loads(zf.read("waypoints.json"))
            waypoints = [tuple(w) for w in waypoints_raw]
        except KeyError:
            waypoints = []

        try:
            ground_truth = GroundTruth.from_dict(
                json.loads(zf.read("ground_truth.json"))
            )
        except KeyError:
            ground_truth = None

        try:
            beacon_signals = [
                BeaconSignal.from_dict(item)
                for item in json.loads(zf.read("beacon_signals.json"))
            ]
        except KeyError:
            beacon_signals = []

        try:
            simulation_runs = [
                SimulationRun.from_dict(item)
                for item in json.loads(zf.read("simulation_runs.json"))
            ]
        except KeyError:
            simulation_runs = []

        image_names = [n for n in zf.namelist() if n.startswith("images/")]
        extracted_images: dict[int, Path] = {}
        for name in image_names:
            zf.extract(name, temp_dir)
            stem = Path(name).stem
            if stem.startswith("level_"):
                try:
                    level_n = int(stem.replace("level_", ""))
                    extracted_images[level_n] = temp_dir / name
                except ValueError:
                    pass

        for level in building.levels:
            if level.n in extracted_images:
                level.floor_plan_path = str(extracted_images[level.n])

    return building, waypoints, temp_dir, ground_truth, beacon_signals, simulation_runs


def cleanup_temp_dir(temp_dir: Path | None) -> None:
    if temp_dir is not None and temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
