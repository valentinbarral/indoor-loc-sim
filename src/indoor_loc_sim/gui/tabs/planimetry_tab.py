from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QComboBox,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QListWidget,
    QAbstractItemView,
    QLineEdit,
    QSplitter,
    QToolBar,
    QCheckBox,
    QColorDialog,
)
from PySide6.QtGui import (
    QIcon,
    QAction,
    QPixmap,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QKeySequence,
    QShortcut,
)

from indoor_loc_sim.core.models import Beacon, Level, Node, Wall, Door
from indoor_loc_sim.gui.state import AppState
from indoor_loc_sim.gui.widgets.floor_plan_canvas import (
    FloorPlanCanvas,
    ToolMode,
    resolve_floor_plan_image_path,
)


class _UndoKind(Enum):
    REMOVE_WALLS = auto()
    REMOVE_DOORS = auto()
    ADD_BEACON = auto()
    REMOVE_BEACON = auto()
    ADD_WALL = auto()
    ADD_DOOR = auto()
    CLEAR_BEACONS = auto()
    ADD_ROOM = auto()


@dataclass
class _UndoEntry:
    kind: _UndoKind
    level_index: int
    data: Any = None


def _make_icon(draw_fn, size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(painter, size)
    painter.end()
    return QIcon(pixmap)


def _icon_select(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#2c3e50"), 2)
    p.setPen(pen)
    p.drawLine(4, 4, 10, 18)
    p.drawLine(10, 18, 14, 12)
    p.drawLine(4, 4, 12, 4)


def _icon_rect_select(p: QPainter, s: int) -> None:
    p.setPen(QPen(QColor("#2980b9"), max(1, s // 10), Qt.PenStyle.DashLine))
    margin = s // 4
    p.drawRect(margin, margin, s - 2 * margin, s - 2 * margin)


def _icon_pan(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#2c3e50"), 1.8)
    p.setPen(pen)
    cx, cy = s // 2, s // 2
    p.drawLine(cx, 3, cx, s - 3)
    p.drawLine(3, cy, s - 3, cy)
    for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
        tip_x = cx + dx * (s // 2 - 3)
        tip_y = cy + dy * (s // 2 - 3)
        p.drawLine(tip_x, tip_y, tip_x - dx * 3 + dy * 2, tip_y - dy * 3 + dx * 2)
        p.drawLine(tip_x, tip_y, tip_x - dx * 3 - dy * 2, tip_y - dy * 3 - dx * 2)


def _icon_beacon(p: QPainter, s: int) -> None:
    p.setPen(QPen(QColor("#e74c3c"), 1.5))
    p.setBrush(QBrush(QColor("#e74c3c")))
    cx, cy = s // 2, s // 2 + 2
    p.drawEllipse(cx - 4, cy - 4, 8, 8)
    p.setBrush(QBrush(QColor(0, 0, 0, 0)))
    p.drawEllipse(cx - 8, cy - 8, 16, 16)
    p.setPen(QPen(QColor("#e74c3c"), 1))
    p.drawLine(cx, cy - 4, cx, 3)


def _icon_wall(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#2c3e50"), 3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(4, s - 4, s - 4, 4)
    p.setBrush(QBrush(QColor("#2c3e50")))
    p.setPen(QPen(Qt.PenStyle.NoPen))
    p.drawEllipse(2, s - 6, 5, 5)
    p.drawEllipse(s - 7, 2, 5, 5)


def _icon_door(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#27ae60"), 3, Qt.PenStyle.DashLine)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(4, s - 4, s - 4, 4)
    p.setBrush(QBrush(QColor("#27ae60")))
    p.setPen(QPen(Qt.PenStyle.NoPen))
    p.drawEllipse(2, s - 6, 5, 5)
    p.drawEllipse(s - 7, 2, 5, 5)


def _icon_room(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#2c3e50"), 2)
    p.setPen(pen)
    m = 4
    p.drawRect(m, m, s - 2 * m, s - 2 * m)


def _icon_fit(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#2c3e50"), 1.5)
    p.setPen(pen)
    m = 3
    corner = 6
    p.drawLine(m, m, m + corner, m)
    p.drawLine(m, m, m, m + corner)
    p.drawLine(s - m, m, s - m - corner, m)
    p.drawLine(s - m, m, s - m, m + corner)
    p.drawLine(m, s - m, m + corner, s - m)
    p.drawLine(m, s - m, m, s - m - corner)
    p.drawLine(s - m, s - m, s - m - corner, s - m)
    p.drawLine(s - m, s - m, s - m, s - m - corner)


def _icon_snap(p: QPainter, s: int) -> None:
    pen = QPen(QColor("#3498db"), 1.2)
    p.setPen(pen)
    step = (s - 4) // 3
    for i in range(4):
        for j in range(4):
            x = 2 + i * step
            y = 2 + j * step
            p.drawEllipse(x - 1, y - 1, 3, 3)
    p.setPen(QPen(QColor("#e74c3c"), 2))
    cx = 2 + step
    cy = 2 + 2 * step
    p.drawLine(cx - 3, cy, cx + 3, cy)
    p.drawLine(cx, cy - 3, cx, cy + 3)


class PlanimetryTab(QWidget):
    floor_plan_visibility_changed = Signal(bool)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state = state
        self._skip_refresh: bool = False
        self._undo_stack: list[_UndoEntry] = []
        self._show_floor_plan: bool = True
        self._setup_ui()
        self._connect_signals()

        self._shortcut_undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._shortcut_undo.activated.connect(self._undo)

        if not self._state.building.levels:
            self._on_add_level()
        else:
            self._refresh_level_list()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # ── Level management ──
        levels_group = QGroupBox("Levels")
        levels_layout = QVBoxLayout(levels_group)

        self._level_list = QListWidget()
        self._level_list.setMaximumHeight(120)
        levels_layout.addWidget(self._level_list)

        level_btn_layout = QHBoxLayout()
        self._btn_add_level = QPushButton("Add Level")
        self._btn_remove_level = QPushButton("Remove")
        level_btn_layout.addWidget(self._btn_add_level)
        level_btn_layout.addWidget(self._btn_remove_level)
        levels_layout.addLayout(level_btn_layout)

        left_layout.addWidget(levels_group)

        # ── Level properties ──
        props_group = QGroupBox("Level Properties")
        props_layout = QVBoxLayout(props_group)

        dim_layout = QHBoxLayout()
        dim_layout.addWidget(QLabel("Width (m):"))
        self._spin_width = QDoubleSpinBox()
        self._spin_width.setRange(1, 1000)
        self._spin_width.setValue(50)
        dim_layout.addWidget(self._spin_width)
        dim_layout.addWidget(QLabel("Height (m):"))
        self._spin_height_dim = QDoubleSpinBox()
        self._spin_height_dim.setRange(1, 1000)
        self._spin_height_dim.setValue(50)
        dim_layout.addWidget(self._spin_height_dim)
        props_layout.addLayout(dim_layout)

        floor_h_layout = QHBoxLayout()
        floor_h_layout.addWidget(QLabel("Floor height (m):"))
        self._spin_floor_height = QDoubleSpinBox()
        self._spin_floor_height.setRange(0, 100)
        self._spin_floor_height.setValue(3.0)
        floor_h_layout.addWidget(self._spin_floor_height)
        props_layout.addLayout(floor_h_layout)

        plan_layout = QHBoxLayout()
        self._btn_load_plan = QPushButton("Load Floor Plan...")
        self._lbl_plan_path = QLabel("No image loaded")
        self._lbl_plan_path.setWordWrap(True)
        plan_layout.addWidget(self._btn_load_plan)
        props_layout.addLayout(plan_layout)
        self._chk_show_plan = QCheckBox("Show floor plan")
        self._chk_show_plan.setChecked(True)
        self._chk_show_plan.setEnabled(False)
        props_layout.addWidget(self._chk_show_plan)
        props_layout.addWidget(self._lbl_plan_path)

        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale (px/m):"))
        self._spin_scale = QDoubleSpinBox()
        self._spin_scale.setRange(0.1, 1000.0)
        self._spin_scale.setValue(10.0)
        self._spin_scale.setSingleStep(1.0)
        self._spin_scale.setDecimals(1)
        self._spin_scale.setToolTip(
            "Pixels per meter — used to convert floor plan image to real dimensions"
        )
        scale_layout.addWidget(self._spin_scale)
        props_layout.addLayout(scale_layout)

        left_layout.addWidget(props_group)

        # ── Beacon list ──
        beacons_group = QGroupBox("Beacons")
        beacons_layout = QVBoxLayout(beacons_group)

        self._beacon_list = QListWidget()
        self._beacon_list.setMaximumHeight(150)
        self._beacon_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._beacon_list.setStyleSheet(
            "QListWidget::item:selected { background: #f39c12; color: black; }"
            "QListWidget::item:selected:!active { background: #f39c12; color: black; }"
        )
        beacons_layout.addWidget(self._beacon_list)

        beacon_label_layout = QHBoxLayout()
        beacon_label_layout.addWidget(QLabel("Label:"))
        self._beacon_label_edit = QLineEdit()
        self._beacon_label_edit.setPlaceholderText("Beacon name")
        beacon_label_layout.addWidget(self._beacon_label_edit)
        beacons_layout.addLayout(beacon_label_layout)

        tx_power_layout = QHBoxLayout()
        tx_power_layout.addWidget(QLabel("Tx Power (dBm):"))
        self._spin_tx_power = QDoubleSpinBox()
        self._spin_tx_power.setRange(-40.0, 30.0)
        self._spin_tx_power.setValue(0.0)
        self._spin_tx_power.setSingleStep(1.0)
        self._spin_tx_power.setDecimals(1)
        tx_power_layout.addWidget(self._spin_tx_power)
        beacons_layout.addLayout(tx_power_layout)

        beacon_btn_layout = QHBoxLayout()
        self._btn_remove_beacon = QPushButton("Remove Selected")
        self._btn_clear_beacons = QPushButton("Clear All")
        beacon_btn_layout.addWidget(self._btn_remove_beacon)
        beacon_btn_layout.addWidget(self._btn_clear_beacons)
        beacons_layout.addLayout(beacon_btn_layout)

        left_layout.addWidget(beacons_group)
        left_layout.addStretch()

        # ── Canvas + toolbar ──
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self._action_select = QAction(_make_icon(_icon_select), "Select (S)", self)
        self._action_select.setCheckable(True)
        self._action_select.setChecked(True)
        self._action_select.setShortcut("S")
        self._action_select.setToolTip("Select / Move items (S)")
        toolbar.addAction(self._action_select)

        self._action_rect_select = QAction(
            _make_icon(_icon_rect_select), "Rect Select (R)", self
        )
        self._action_rect_select.setCheckable(True)
        self._action_rect_select.setShortcut("R")
        self._action_rect_select.setToolTip("Rectangle select (R)")
        toolbar.addAction(self._action_rect_select)

        self._action_pan = QAction(_make_icon(_icon_pan), "Pan (H)", self)
        self._action_pan.setCheckable(True)
        self._action_pan.setShortcut("H")
        self._action_pan.setToolTip("Pan / Scroll view (H)")
        toolbar.addAction(self._action_pan)

        toolbar.addSeparator()

        self._action_beacon = QAction(_make_icon(_icon_beacon), "Beacon (B)", self)
        self._action_beacon.setCheckable(True)
        self._action_beacon.setShortcut("B")
        self._action_beacon.setToolTip("Place beacon (B)")
        toolbar.addAction(self._action_beacon)

        self._action_wall = QAction(_make_icon(_icon_wall), "Wall (W)", self)
        self._action_wall.setCheckable(True)
        self._action_wall.setShortcut("W")
        self._action_wall.setToolTip(
            "Draw wall — click to chain, Right-click/Esc to stop (W)"
        )
        toolbar.addAction(self._action_wall)

        self._wall_color = QColor("#2c3e50")
        self._btn_wall_color = QPushButton()
        self._btn_wall_color.setFixedSize(24, 24)
        self._btn_wall_color.setToolTip("Wall color")
        self._update_wall_color_icon()
        toolbar.addWidget(self._btn_wall_color)

        self._action_door = QAction(_make_icon(_icon_door), "Door (D)", self)
        self._action_door.setCheckable(True)
        self._action_door.setShortcut("D")
        self._action_door.setToolTip(
            "Place door — click on a wall to add a 0.7m door (D)"
        )
        toolbar.addAction(self._action_door)

        self._action_room = QAction(_make_icon(_icon_room), "Room (G)", self)
        self._action_room.setCheckable(True)
        self._action_room.setShortcut("G")
        self._action_room.setToolTip(
            "Draw room — drag a rectangle to create 4 walls (G)"
        )
        toolbar.addAction(self._action_room)

        toolbar.addSeparator()

        self._action_fit = QAction(_make_icon(_icon_fit), "Fit View (F)", self)
        self._action_fit.setShortcut("F")
        self._action_fit.setToolTip("Fit view to plan (F)")
        toolbar.addAction(self._action_fit)

        toolbar.addSeparator()

        self._action_snap = QAction(_make_icon(_icon_snap), "Snap Grid", self)
        self._action_snap.setCheckable(True)
        self._action_snap.setChecked(True)
        self._action_snap.setToolTip("Toggle snap-to-grid")
        toolbar.addAction(self._action_snap)

        self._snap_spacing_spin = QDoubleSpinBox()
        self._snap_spacing_spin.setRange(0.1, 10.0)
        self._snap_spacing_spin.setValue(0.5)
        self._snap_spacing_spin.setSingleStep(0.1)
        self._snap_spacing_spin.setDecimals(1)
        self._snap_spacing_spin.setSuffix(" m")
        self._snap_spacing_spin.setToolTip("Snap grid spacing")
        self._snap_spacing_spin.setFixedWidth(80)
        toolbar.addWidget(self._snap_spacing_spin)

        self._tool_actions = [
            self._action_select,
            self._action_rect_select,
            self._action_pan,
            self._action_beacon,
            self._action_wall,
            self._action_door,
            self._action_room,
        ]

        canvas_layout.addWidget(toolbar)

        self._canvas = FloorPlanCanvas()
        canvas_layout.addWidget(self._canvas)

        splitter.addWidget(left_panel)
        splitter.addWidget(canvas_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self._btn_add_level.clicked.connect(self._on_add_level)
        self._btn_remove_level.clicked.connect(self._on_remove_level)
        self._level_list.currentRowChanged.connect(self._on_level_selected)
        self._beacon_list.currentRowChanged.connect(self._on_beacon_list_selected)

        self._btn_load_plan.clicked.connect(self._on_load_plan)
        self._chk_show_plan.toggled.connect(self._on_show_plan_toggled)
        self._spin_width.valueChanged.connect(self._on_dimensions_changed)
        self._spin_height_dim.valueChanged.connect(self._on_dimensions_changed)
        self._spin_floor_height.valueChanged.connect(self._on_floor_height_changed)
        self._spin_scale.valueChanged.connect(self._on_scale_changed)

        self._btn_remove_beacon.clicked.connect(self._on_remove_beacon)
        self._btn_clear_beacons.clicked.connect(self._on_clear_beacons)

        self._action_select.triggered.connect(lambda: self._set_tool(ToolMode.SELECT))
        self._action_rect_select.triggered.connect(
            lambda: self._set_tool(ToolMode.RECT_SELECT)
        )
        self._action_pan.triggered.connect(lambda: self._set_tool(ToolMode.PAN))
        self._action_beacon.triggered.connect(
            lambda: self._set_tool(ToolMode.PLACE_BEACON)
        )
        self._action_wall.triggered.connect(lambda: self._set_tool(ToolMode.DRAW_WALL))
        self._action_door.triggered.connect(lambda: self._set_tool(ToolMode.DRAW_DOOR))
        self._action_room.triggered.connect(lambda: self._set_tool(ToolMode.DRAW_ROOM))
        self._action_fit.triggered.connect(self._fit_view)

        self._action_snap.toggled.connect(self._on_snap_toggled)
        self._snap_spacing_spin.valueChanged.connect(self._on_snap_spacing_changed)
        self._btn_wall_color.clicked.connect(self._on_wall_color_clicked)

        self._canvas.beacon_placed.connect(self._on_beacon_placed)
        self._canvas.beacon_moved.connect(self._on_beacon_moved)
        self._canvas.beacon_move_finished.connect(self._on_beacon_move_finished)
        self._canvas.beacon_selected.connect(self._on_beacon_selected)
        self._canvas.selection_cleared.connect(self._clear_beacon_list_selection)
        self._canvas.wall_drawn.connect(self._on_wall_drawn)
        self._canvas.door_drawn.connect(self._on_door_drawn)
        self._canvas.room_drawn.connect(self._on_room_drawn)
        self._canvas.beacon_deleted.connect(self._on_beacon_deleted)
        self._canvas.walls_deleted.connect(self._on_walls_deleted)
        self._canvas.doors_deleted.connect(self._on_doors_deleted)

        self._state.building_changed.connect(self._refresh_all)

    # ── Snap controls ──

    def _on_snap_toggled(self, checked: bool) -> None:
        self._canvas.set_snap_enabled(checked)

    def _on_snap_spacing_changed(self, value: float) -> None:
        self._canvas.set_snap_spacing(value)

    def _on_show_plan_toggled(self, checked: bool) -> None:
        self._show_floor_plan = checked
        self._canvas.set_floor_plan_visible(checked)
        self.floor_plan_visibility_changed.emit(checked)

    # ── Wall color ──

    def _update_wall_color_icon(self) -> None:
        px = QPixmap(20, 20)
        px.fill(self._wall_color)
        self._btn_wall_color.setIcon(QIcon(px))
        self._btn_wall_color.setIconSize(QSize(20, 20))

    def _on_wall_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._wall_color, self, "Wall Color")
        if color.isValid():
            self._wall_color = color
            self._update_wall_color_icon()
            self._canvas.set_wall_color(color)

    # ── Undo ──

    def _push_undo(self, entry: _UndoEntry) -> None:
        self._undo_stack.append(entry)

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        levels = self._state.building.levels
        if entry.level_index < 0 or entry.level_index >= len(levels):
            return
        level = levels[entry.level_index]

        if entry.kind == _UndoKind.ADD_BEACON:
            beacon = entry.data
            if beacon in level.beacons:
                level.beacons.remove(beacon)
            self._refresh_beacon_list()
            self._refresh_canvas_beacons()

        elif entry.kind == _UndoKind.REMOVE_BEACON:
            index, beacon = entry.data
            index = min(index, len(level.beacons))
            level.beacons.insert(index, beacon)
            self._refresh_beacon_list()
            self._refresh_canvas_beacons()

        elif entry.kind == _UndoKind.ADD_WALL:
            if level.walls and level.walls[-1] is entry.data:
                level.walls.pop()
                self._canvas.remove_last_wall()

        elif entry.kind == _UndoKind.ADD_DOOR:
            if level.doors and level.doors[-1] is entry.data:
                level.doors.pop()
                self._canvas.remove_last_door()

        elif entry.kind == _UndoKind.REMOVE_WALLS:
            for idx, wall in sorted(entry.data, key=lambda x: x[0]):
                level.walls.insert(idx, wall)
            self._refresh_canvas()

        elif entry.kind == _UndoKind.REMOVE_DOORS:
            for idx, door in sorted(entry.data, key=lambda x: x[0]):
                level.doors.insert(idx, door)
            self._refresh_canvas()

        elif entry.kind == _UndoKind.CLEAR_BEACONS:
            old_beacons = entry.data
            level.beacons.extend(old_beacons)
            self._refresh_beacon_list()
            self._refresh_canvas_beacons()

        elif entry.kind == _UndoKind.ADD_ROOM:
            walls = entry.data
            for wall in walls:
                if wall in level.walls:
                    level.walls.remove(wall)
            self._refresh_canvas()

        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False

    # ── Tool selection ──

    def _set_tool(self, mode: ToolMode) -> None:
        for action in self._tool_actions:
            action.setChecked(False)
        if mode == ToolMode.SELECT:
            self._action_select.setChecked(True)
        elif mode == ToolMode.PAN:
            self._action_pan.setChecked(True)
        elif mode == ToolMode.RECT_SELECT:
            self._action_rect_select.setChecked(True)

        elif mode == ToolMode.PLACE_BEACON:
            self._action_beacon.setChecked(True)
        elif mode == ToolMode.DRAW_WALL:
            self._action_wall.setChecked(True)
        elif mode == ToolMode.DRAW_DOOR:
            self._action_door.setChecked(True)
        elif mode == ToolMode.DRAW_ROOM:
            self._action_room.setChecked(True)
        self._canvas.set_tool_mode(mode)

    def _current_level(self) -> Level | None:
        idx = self._level_list.currentRow()
        if 0 <= idx < len(self._state.building.levels):
            return self._state.building.levels[idx]
        return None

    # ── Level management ──

    def _on_add_level(self) -> None:
        n = len(self._state.building.levels)
        level = Level(n=n, dimensions=(50.0, 50.0), height=3.0)
        self._state.building.levels.append(level)
        self._refresh_level_list()
        self._level_list.setCurrentRow(n)
        self._state.building_changed.emit()

    def _on_remove_level(self) -> None:
        idx = self._level_list.currentRow()
        if 0 <= idx < len(self._state.building.levels):
            self._state.building.levels.pop(idx)
            self._refresh_level_list()
            self._state.building_changed.emit()

    def _on_level_selected(self, row: int) -> None:
        if 0 <= row < len(self._state.building.levels):
            self._state.current_level_index = row
            self._refresh_canvas()
            self._refresh_properties()
            self._refresh_beacon_list()

    def _on_load_plan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Floor Plan", "", "Images (*.png *.jpg *.bmp)"
        )
        if path:
            level = self._current_level()
            if level:
                level.floor_plan_path = path
                self._lbl_plan_path.setText(Path(path).name)
                self._apply_floor_plan(level)

    def _apply_floor_plan(self, level: Level) -> None:
        if not level.floor_plan_path:
            return
        pixmap = QPixmap(resolve_floor_plan_image_path(level.floor_plan_path))
        if pixmap.isNull():
            return
        scale = level.px_per_meter
        w_m = pixmap.width() / scale
        h_m = pixmap.height() / scale
        level.dimensions = (w_m, h_m)
        self._spin_width.blockSignals(True)
        self._spin_height_dim.blockSignals(True)
        self._spin_width.setValue(w_m)
        self._spin_height_dim.setValue(h_m)
        self._spin_width.blockSignals(False)
        self._spin_height_dim.blockSignals(False)
        self._canvas.load_floor_plan(level.floor_plan_path, level.dimensions)
        self._canvas.set_floor_plan_visible(self._show_floor_plan)
        self._chk_show_plan.setEnabled(True)

    def _on_scale_changed(self, value: float) -> None:
        level = self._current_level()
        if level:
            level.px_per_meter = value
            if level.floor_plan_path:
                self._apply_floor_plan(level)

    def _on_dimensions_changed(self) -> None:
        level = self._current_level()
        if level:
            level.dimensions = (self._spin_width.value(), self._spin_height_dim.value())
            if level.floor_plan_path:
                self._canvas.load_floor_plan(level.floor_plan_path, level.dimensions)
            else:
                self._canvas.set_dimensions(level.dimensions)

    def _on_floor_height_changed(self) -> None:
        level = self._current_level()
        if level:
            level.height = self._spin_floor_height.value()

    # ── Beacon handlers ──

    def _on_beacon_placed(self, mx: float, my: float) -> None:
        level = self._current_level()
        if level is None:
            return
        label = self._beacon_label_edit.text().strip()
        if not label:
            label = f"B-{len(level.beacons) + 1}"
        z = level.n * level.height
        beacon = Beacon(
            x=mx,
            y=my,
            z=z,
            label=label,
            level_index=level.n,
            tx_power=self._spin_tx_power.value(),
        )
        level.beacons.append(beacon)
        self._push_undo(
            _UndoEntry(_UndoKind.ADD_BEACON, self._level_list.currentRow(), beacon)
        )
        self._refresh_beacon_list()
        self._refresh_canvas_beacons()
        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False
        self._beacon_label_edit.clear()

    def _on_walls_deleted(self, walls: list) -> None:
        level = self._current_level()
        if level is None:
            return
        level_idx = self._level_list.currentRow()
        indices_and_walls = []
        for wall in walls:
            if wall in level.walls:
                indices_and_walls.append((level.walls.index(wall), wall))

        indices_and_walls.sort(key=lambda x: x[0], reverse=True)
        for idx, wall in indices_and_walls:
            level.walls.pop(idx)

        if indices_and_walls:
            self._undo_stack.append(
                _UndoEntry(_UndoKind.REMOVE_WALLS, level_idx, indices_and_walls)
            )
            self._skip_refresh = True
            self._state.building_changed.emit()
            self._skip_refresh = False

    def _on_doors_deleted(self, doors: list) -> None:
        level = self._current_level()
        if level is None:
            return
        level_idx = self._level_list.currentRow()
        indices_and_doors = []
        for door in doors:
            if door in level.doors:
                indices_and_doors.append((level.doors.index(door), door))

        indices_and_doors.sort(key=lambda x: x[0], reverse=True)
        for idx, door in indices_and_doors:
            level.doors.pop(idx)

        if indices_and_doors:
            self._undo_stack.append(
                _UndoEntry(_UndoKind.REMOVE_DOORS, level_idx, indices_and_doors)
            )
            self._skip_refresh = True
            self._state.building_changed.emit()
            self._skip_refresh = False

    def _on_beacon_moved(self, index: int, mx: float, my: float) -> None:
        level = self._current_level()
        if level is None:
            return
        if 0 <= index < len(level.beacons):
            level.beacons[index].x = mx
            level.beacons[index].y = my

    def _on_beacon_move_finished(self, index: int, mx: float, my: float) -> None:
        level = self._current_level()
        if level is None:
            return
        if 0 <= index < len(level.beacons):
            level.beacons[index].x = mx
            level.beacons[index].y = my
            self._refresh_beacon_list()
            self._skip_refresh = True
            self._state.building_changed.emit()
            self._skip_refresh = False

    def _on_beacon_selected(self, index: int) -> None:
        if 0 <= index < self._beacon_list.count():
            item = self._beacon_list.item(index)
            if item is not None:
                self._beacon_list.blockSignals(True)
                self._beacon_list.clearSelection()
                self._beacon_list.setCurrentRow(index)
                self._beacon_list.setCurrentItem(item)
                item.setSelected(True)
                self._beacon_list.blockSignals(False)
                for i in range(self._beacon_list.count()):
                    other = self._beacon_list.item(i)
                    if other is None:
                        continue
                    if i == index:
                        other.setBackground(QColor("#f39c12"))
                        other.setForeground(QColor("#000000"))
                    else:
                        other.setBackground(QBrush())
                        other.setForeground(QBrush())
                self._beacon_list.scrollToItem(item)

    def _clear_beacon_list_selection(self) -> None:
        self._beacon_list.blockSignals(True)
        self._beacon_list.clearSelection()
        self._beacon_list.setCurrentRow(-1)
        self._beacon_list.blockSignals(False)
        for i in range(self._beacon_list.count()):
            item = self._beacon_list.item(i)
            if item is not None:
                item.setBackground(QBrush())
                item.setForeground(QBrush())

    def _on_beacon_list_selected(self, row: int) -> None:
        if row >= 0:
            self._canvas.select_beacon_by_index(row)
        else:
            self._clear_beacon_list_selection()

    def _on_wall_drawn(self, x1: float, y1: float, x2: float, y2: float) -> None:
        level = self._current_level()
        if level is None:
            return
        wall = Wall(start=Node(x=x1, y=y1), end=Node(x=x2, y=y2))
        level.walls.append(wall)
        self._canvas.add_wall(wall)
        self._push_undo(
            _UndoEntry(_UndoKind.ADD_WALL, self._level_list.currentRow(), wall)
        )
        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False

    def _on_door_drawn(self, x1: float, y1: float, x2: float, y2: float) -> None:
        level = self._current_level()
        if level is None:
            return
        door = Door(start=Node(x=x1, y=y1), end=Node(x=x2, y=y2))
        level.doors.append(door)
        self._canvas.add_door(door)
        self._push_undo(
            _UndoEntry(_UndoKind.ADD_DOOR, self._level_list.currentRow(), door)
        )
        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False

    def _on_room_drawn(self, x1: float, y1: float, x2: float, y2: float) -> None:
        level = self._current_level()
        if level is None:
            return
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        walls: list[Wall] = []
        for i in range(4):
            sx, sy = corners[i]
            ex, ey = corners[(i + 1) % 4]
            wall = Wall(start=Node(x=sx, y=sy), end=Node(x=ex, y=ey))
            level.walls.append(wall)
            self._canvas.add_wall(wall)
            walls.append(wall)
        self._push_undo(
            _UndoEntry(_UndoKind.ADD_ROOM, self._level_list.currentRow(), walls)
        )
        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False

    def _on_remove_beacon(self) -> None:
        level = self._current_level()
        if level is None:
            return
        row = self._beacon_list.currentRow()
        if 0 <= row < len(level.beacons):
            beacon = level.beacons.pop(row)
            self._push_undo(
                _UndoEntry(
                    _UndoKind.REMOVE_BEACON,
                    self._level_list.currentRow(),
                    (row, beacon),
                )
            )
            self._refresh_beacon_list()
            self._refresh_canvas_beacons()
            self._skip_refresh = True
            self._state.building_changed.emit()
            self._skip_refresh = False

    def _on_beacon_deleted(self, index: int) -> None:
        level = self._current_level()
        if level is None:
            return
        if 0 <= index < len(level.beacons):
            beacon = level.beacons.pop(index)
            self._push_undo(
                _UndoEntry(
                    _UndoKind.REMOVE_BEACON,
                    self._level_list.currentRow(),
                    (index, beacon),
                )
            )
            self._refresh_beacon_list()
            self._refresh_canvas_beacons()
            self._skip_refresh = True
            self._state.building_changed.emit()
            self._skip_refresh = False

    def _on_clear_beacons(self) -> None:
        level = self._current_level()
        if level is None:
            return
        old_beacons = list(level.beacons)
        level.beacons.clear()
        if old_beacons:
            self._push_undo(
                _UndoEntry(
                    _UndoKind.CLEAR_BEACONS,
                    self._level_list.currentRow(),
                    old_beacons,
                )
            )
        self._refresh_beacon_list()
        self._refresh_canvas_beacons()
        self._skip_refresh = True
        self._state.building_changed.emit()
        self._skip_refresh = False

    # ── Refresh helpers ──

    def _refresh_level_list(self) -> None:
        self._level_list.clear()
        for level in self._state.building.levels:
            self._level_list.addItem(f"Level {level.n}")
        if self._state.building.levels:
            self._level_list.setCurrentRow(
                min(
                    self._state.current_level_index,
                    len(self._state.building.levels) - 1,
                )
            )

    def _refresh_properties(self) -> None:
        level = self._current_level()
        if level:
            self._spin_width.blockSignals(True)
            self._spin_height_dim.blockSignals(True)
            self._spin_floor_height.blockSignals(True)
            self._spin_scale.blockSignals(True)
            self._chk_show_plan.blockSignals(True)
            self._spin_width.setValue(level.dimensions[0])
            self._spin_height_dim.setValue(level.dimensions[1])
            self._spin_floor_height.setValue(level.height)
            self._spin_scale.setValue(level.px_per_meter)
            self._spin_width.blockSignals(False)
            self._spin_height_dim.blockSignals(False)
            self._spin_floor_height.blockSignals(False)
            self._spin_scale.blockSignals(False)
            if level.floor_plan_path:
                self._lbl_plan_path.setText(Path(level.floor_plan_path).name)
                self._chk_show_plan.setEnabled(True)
                self._chk_show_plan.setChecked(self._show_floor_plan)
            else:
                self._lbl_plan_path.setText("No image loaded")
                self._chk_show_plan.setChecked(False)
                self._chk_show_plan.setEnabled(False)
            self._chk_show_plan.blockSignals(False)

    def _refresh_beacon_list(self) -> None:
        self._beacon_list.clear()
        level = self._current_level()
        if level:
            for b in level.beacons:
                self._beacon_list.addItem(
                    f"{b.label} ({b.x:.1f}, {b.y:.1f}) {b.tx_power:+.0f} dBm"
                )

    def _refresh_canvas(self) -> None:
        self._canvas.clear_all()
        self._canvas.clear_floor_plan()
        level = self._current_level()
        if level is None:
            return
        if level.floor_plan_path:
            self._canvas.load_floor_plan(level.floor_plan_path, level.dimensions)
            self._canvas.set_floor_plan_visible(self._show_floor_plan)
        else:
            self._canvas.set_dimensions(level.dimensions)
        for wall in level.walls:
            self._canvas.add_wall(wall)
        for door in level.doors:
            self._canvas.add_door(door)
        self._refresh_canvas_beacons()

    def _refresh_canvas_beacons(self) -> None:
        self._canvas.clear_beacons()
        level = self._current_level()
        if level:
            for i, b in enumerate(level.beacons):
                self._canvas.add_beacon(b, i)

    def _refresh_all(self) -> None:
        if self._skip_refresh:
            return
        self._refresh_level_list()
        self._refresh_canvas()
        self._refresh_beacon_list()

    def _fit_view(self) -> None:
        self._canvas.fitInView(
            self._canvas.scene().sceneRect(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )

    @property
    def canvas(self) -> FloorPlanCanvas:
        return self._canvas
