from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QDoubleSpinBox,
    QSplitter,
    QToolBar,
    QListWidget,
)
from PySide6.QtGui import QColor

from indoor_loc_sim.core.trajectory import generate_ground_truth
from indoor_loc_sim.gui.state import AppState
from indoor_loc_sim.gui.widgets.floor_plan_canvas import FloorPlanCanvas, ToolMode


class TrajectoryTab(QWidget):
    def __init__(
        self, state: AppState, planimetry_canvas: FloorPlanCanvas, parent=None
    ):
        super().__init__(parent)
        self._state = state
        self._planimetry_canvas = planimetry_canvas
        self._canvas_dirty = True
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        params_group = QGroupBox("Trajectory Parameters")
        params_layout = QVBoxLayout(params_group)

        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Walking speed (m/s):"))
        self._spin_speed = QDoubleSpinBox()
        self._spin_speed.setRange(0.1, 10.0)
        self._spin_speed.setValue(1.0)
        self._spin_speed.setSingleStep(0.1)
        speed_layout.addWidget(self._spin_speed)
        params_layout.addLayout(speed_layout)

        freq_layout = QHBoxLayout()
        freq_layout.addWidget(QLabel("Sampling frequency (Hz):"))
        self._spin_freq = QDoubleSpinBox()
        self._spin_freq.setRange(0.5, 100.0)
        self._spin_freq.setValue(1.0)
        freq_layout.addWidget(self._spin_freq)
        params_layout.addLayout(freq_layout)

        left_layout.addWidget(params_group)

        waypoints_group = QGroupBox("Waypoints")
        waypoints_layout = QVBoxLayout(waypoints_group)

        self._waypoint_list = QListWidget()
        self._waypoint_list.setMaximumHeight(200)
        waypoints_layout.addWidget(self._waypoint_list)

        self._lbl_count = QLabel("0 waypoints defined")
        waypoints_layout.addWidget(self._lbl_count)

        left_layout.addWidget(waypoints_group)

        btn_layout = QVBoxLayout()
        self._btn_draw = QPushButton("Draw Trajectory Mode")
        self._btn_draw.setCheckable(True)
        btn_layout.addWidget(self._btn_draw)

        self._btn_generate = QPushButton("Generate!")
        self._btn_generate.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 8px; }"
        )
        btn_layout.addWidget(self._btn_generate)

        self._btn_clear = QPushButton("Clear Trajectory")
        btn_layout.addWidget(self._btn_clear)

        left_layout.addLayout(btn_layout)

        info_group = QGroupBox("Trajectory Info")
        info_layout = QVBoxLayout(info_group)
        self._lbl_info = QLabel("No trajectory generated")
        self._lbl_info.setWordWrap(True)
        info_layout.addWidget(self._lbl_info)
        left_layout.addWidget(info_group)

        left_layout.addStretch()

        self._canvas = FloorPlanCanvas()
        splitter.addWidget(left_panel)
        splitter.addWidget(self._canvas)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self._btn_draw.toggled.connect(self._on_draw_toggled)
        self._btn_generate.clicked.connect(self._on_generate)
        self._btn_clear.clicked.connect(self._on_clear)
        self._canvas.waypoint_placed.connect(self._on_waypoint_placed)
        self._state.building_changed.connect(self._mark_canvas_dirty)

    def _mark_canvas_dirty(self) -> None:
        self._canvas_dirty = True

    def ensure_canvas_up_to_date(self) -> None:
        if self._canvas_dirty:
            self._refresh_canvas()

    def _on_draw_toggled(self, checked: bool) -> None:
        if checked:
            self._canvas.set_tool_mode(ToolMode.DRAW_TRAJECTORY)
            self._btn_draw.setText("Drawing... (click on map)")
        else:
            self._canvas.set_tool_mode(ToolMode.SELECT)
            self._btn_draw.setText("Draw Trajectory Mode")

    def _on_waypoint_placed(self, mx: float, my: float) -> None:
        level = None
        idx = self._state.current_level_index
        if 0 <= idx < len(self._state.building.levels):
            level = self._state.building.levels[idx]
        z = level.n * level.height if level else 0.0

        self._state.waypoints.append((mx, my, z))
        self._canvas.add_waypoint(mx, my)
        self._refresh_waypoint_list()

    def _on_generate(self) -> None:
        if len(self._state.waypoints) < 2:
            self._lbl_info.setText("Need at least 2 waypoints")
            return

        gt = generate_ground_truth(
            waypoints=self._state.waypoints,
            frequency=self._spin_freq.value(),
            walking_speed=self._spin_speed.value(),
        )
        self._state.set_ground_truth(gt)

        self._canvas.draw_real_trajectory(gt.events)
        self._planimetry_canvas.draw_real_trajectory(gt.events)

        duration = gt.events[-1].t if gt.events else 0
        self._lbl_info.setText(
            f"Generated: {len(gt.events)} points\n"
            f"Duration: {duration:.1f} s\n"
            f"Frequency: {gt.frequency} Hz"
        )

    def _on_clear(self) -> None:
        self._state.clear_trajectory()
        self._canvas.clear_waypoints()
        self._canvas.clear_all_trajectories()
        self._planimetry_canvas.clear_waypoints()
        self._planimetry_canvas.clear_all_trajectories()
        self._waypoint_list.clear()
        self._lbl_count.setText("0 waypoints defined")
        self._lbl_info.setText("No trajectory generated")

    def _refresh_waypoint_list(self) -> None:
        self._waypoint_list.clear()
        for i, (x, y, z) in enumerate(self._state.waypoints):
            self._waypoint_list.addItem(f"P{i + 1}: ({x:.1f}, {y:.1f}, {z:.1f})")
        self._lbl_count.setText(f"{len(self._state.waypoints)} waypoints defined")

    def _refresh_canvas(self) -> None:
        self._canvas_dirty = False
        self._canvas.clear_all()
        self._canvas.clear_floor_plan()
        level_idx = self._state.current_level_index
        if 0 <= level_idx < len(self._state.building.levels):
            level = self._state.building.levels[level_idx]
            if level.floor_plan_path:
                self._canvas.load_floor_plan(level.floor_plan_path, level.dimensions)
            else:
                self._canvas.set_dimensions(level.dimensions)
            for i, b in enumerate(level.beacons):
                self._canvas.add_beacon(b, i)
            for wall in level.walls:
                self._canvas.add_wall(wall)
            for door in level.doors:
                self._canvas.add_door(door)

        for x, y, _ in self._state.waypoints:
            self._canvas.add_waypoint(x, y)
        if self._state.ground_truth:
            self._canvas.draw_real_trajectory(self._state.ground_truth.events)
