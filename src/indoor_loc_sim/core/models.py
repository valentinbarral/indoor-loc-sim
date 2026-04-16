from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Node:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    label: str = ""

    @property
    def r(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> Node:
        return cls(**d)


@dataclass
class Beacon(Node):
    frequency: float = 1.0
    level_index: int = 0
    tx_power: float = 0.0

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(
            {
                "frequency": self.frequency,
                "level_index": self.level_index,
                "tx_power": self.tx_power,
            }
        )
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Beacon:
        return cls(
            x=d.get("x", 0.0),
            y=d.get("y", 0.0),
            z=d.get("z", 0.0),
            label=d.get("label", ""),
            frequency=d.get("frequency", 1.0),
            level_index=d.get("level_index", 0),
            tx_power=d.get("tx_power", 0.0),
        )


@dataclass
class Wall:
    start: Node = field(default_factory=Node)
    end: Node = field(default_factory=Node)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Wall:
        return cls(
            start=Node.from_dict(d["start"]),
            end=Node.from_dict(d["end"]),
            label=d.get("label", ""),
        )


@dataclass
class Door:
    start: Node = field(default_factory=Node)
    end: Node = field(default_factory=Node)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Door:
        return cls(
            start=Node.from_dict(d["start"]),
            end=Node.from_dict(d["end"]),
            label=d.get("label", ""),
        )


@dataclass
class Stairs:
    position: Node = field(default_factory=Node)
    connects_levels: tuple[int, int] = (0, 1)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "position": self.position.to_dict(),
            "connects_levels": list(self.connects_levels),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Stairs:
        return cls(
            position=Node.from_dict(d["position"]),
            connects_levels=tuple(d["connects_levels"]),
            label=d.get("label", ""),
        )


@dataclass
class Elevator:
    position: Node = field(default_factory=Node)
    connects_levels: tuple[int, ...] = (0, 1)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "position": self.position.to_dict(),
            "connects_levels": list(self.connects_levels),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Elevator:
        return cls(
            position=Node.from_dict(d["position"]),
            connects_levels=tuple(d["connects_levels"]),
            label=d.get("label", ""),
        )


@dataclass
class Level:
    n: int = 0
    dimensions: tuple[float, float] = (50.0, 50.0)
    height: float = 3.0
    px_per_meter: float = 10.0
    nodes: list[Node] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)
    beacons: list[Beacon] = field(default_factory=list)
    stairs: list[Stairs] = field(default_factory=list)
    elevators: list[Elevator] = field(default_factory=list)
    floor_plan_path: str = ""

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "dimensions": list(self.dimensions),
            "height": self.height,
            "px_per_meter": self.px_per_meter,
            "nodes": [n.to_dict() for n in self.nodes],
            "walls": [w.to_dict() for w in self.walls],
            "doors": [d.to_dict() for d in self.doors],
            "beacons": [b.to_dict() for b in self.beacons],
            "stairs": [s.to_dict() for s in self.stairs],
            "elevators": [e.to_dict() for e in self.elevators],
            "floor_plan_path": self.floor_plan_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Level:
        return cls(
            n=d["n"],
            dimensions=tuple(d["dimensions"]),
            height=d.get("height", 3.0),
            px_per_meter=d.get("px_per_meter", 10.0),
            nodes=[Node.from_dict(n) for n in d.get("nodes", [])],
            walls=[Wall.from_dict(w) for w in d.get("walls", [])],
            doors=[Door.from_dict(dd) for dd in d.get("doors", [])],
            beacons=[Beacon.from_dict(b) for b in d.get("beacons", [])],
            stairs=[Stairs.from_dict(s) for s in d.get("stairs", [])],
            elevators=[Elevator.from_dict(e) for e in d.get("elevators", [])],
            floor_plan_path=d.get("floor_plan_path", ""),
        )


@dataclass
class Building:
    levels: list[Level] = field(default_factory=list)
    label: str = ""

    def all_beacons(self) -> list[Beacon]:
        result = []
        for level in self.levels:
            result.extend(level.beacons)
        return result

    def beacons_on_level(self, level_index: int) -> list[Beacon]:
        for level in self.levels:
            if level.n == level_index:
                return level.beacons
        return []

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "levels": [lv.to_dict() for lv in self.levels],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Building:
        return cls(
            label=d.get("label", ""),
            levels=[Level.from_dict(lv) for lv in d.get("levels", [])],
        )

    def save(self, path: str | Path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> Building:
        with open(path) as f:
            return cls.from_dict(json.load(f))
