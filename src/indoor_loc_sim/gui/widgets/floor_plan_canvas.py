from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import tempfile
from enum import Enum, auto
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QPoint
from PySide6.QtGui import (
    QPixmap,
    QPen,
    QBrush,
    QColor,
    QPainter,
    QFont,
    QWheelEvent,
    QImage,
)
from PySide6.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
    QGraphicsRectItem,
    QGraphicsItem,
    QGraphicsSceneHoverEvent,
)
from PySide6.QtGui import QPainterPath

from indoor_loc_sim.core.models import Beacon, Node, Wall, Door
from indoor_loc_sim.core.trajectory import TrajectoryPoint


class ToolMode(Enum):
    SELECT = auto()
    RECT_SELECT = auto()
    PAN = auto()
    PLACE_BEACON = auto()
    DRAW_WALL = auto()
    DRAW_DOOR = auto()
    DRAW_TRAJECTORY = auto()
    DRAW_ROOM = auto()


PIXELS_PER_METER = 10.0

BEACON_RADIUS = 6
BEACON_COLOR = QColor("#e74c3c")
BEACON_SELECTED_COLOR = QColor("#f39c12")
WALL_COLOR = QColor("#2c3e50")
DOOR_COLOR = QColor("#27ae60")
TRAJECTORY_REAL_COLOR = QColor("#3498db")
TRAJECTORY_ESTIMATED_COLOR = QColor("#2c3e50")
WAYPOINT_COLOR = QColor("#9b59b6")

GRID_COLOR = QColor(200, 200, 200, 80)
GRID_LABEL_COLOR = QColor(140, 140, 140)
BG_COLOR = QColor(245, 245, 245)
SNAP_CURSOR_COLOR = QColor(255, 80, 80, 180)

FP_RADIO_MAP_COLOR = QColor(180, 180, 180, 120)
FP_RADIO_MAP_HIGHLIGHT = QColor(255, 165, 0, 200)
FP_TRAJECTORY_COLOR = QColor(52, 152, 219, 220)
FP_ESTIMATED_COLOR = QColor(231, 76, 60, 200)
FP_RADIO_MAP_RADIUS = 3.0
FP_TRAJECTORY_RADIUS = 5.0

_VIRIDIS_STOPS = [
    (0.0, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.5, (33, 145, 140)),
    (0.75, (94, 201, 98)),
    (1.0, (253, 231, 37)),
]

_SANITIZED_FLOORPLAN_DIR = Path(tempfile.gettempdir()) / "ilsim_floorplan_cache"


def resolve_floor_plan_image_path(image_path: str) -> str:
    path = Path(image_path)
    if path.suffix.lower() != ".png":
        return image_path

    convert_bin = shutil.which("convert")
    if convert_bin is None:
        return image_path

    try:
        cache_key = hashlib.sha256(
            f"{path.resolve()}:{path.stat().st_mtime_ns}".encode()
        ).hexdigest()
        _SANITIZED_FLOORPLAN_DIR.mkdir(parents=True, exist_ok=True)
        sanitized_path = _SANITIZED_FLOORPLAN_DIR / f"{cache_key}.png"
        if not sanitized_path.exists():
            subprocess.run(
                [convert_bin, str(path), "-strip", f"png32:{sanitized_path}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return str(sanitized_path)
    except Exception:
        return image_path


def _viridis_rgb(t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    for i in range(len(_VIRIDIS_STOPS) - 1):
        t0, c0 = _VIRIDIS_STOPS[i]
        t1, c1 = _VIRIDIS_STOPS[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return (
                int(c0[0] + f * (c1[0] - c0[0])),
                int(c0[1] + f * (c1[1] - c0[1])),
                int(c0[2] + f * (c1[2] - c0[2])),
            )
    return _VIRIDIS_STOPS[-1][1]


class _FpTrajectoryDot(QGraphicsEllipseItem):
    def __init__(
        self,
        point_index: int,
        neighbor_indices: np.ndarray,
        radio_map_items: list[QGraphicsEllipseItem],
        estimated_pos_item: QGraphicsEllipseItem | None,
    ):
        r = FP_TRAJECTORY_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self._point_index = point_index
        self._neighbor_indices = neighbor_indices
        self._radio_map_items = radio_map_items
        self._estimated_pos_item = estimated_pos_item
        self.setBrush(QBrush(FP_TRAJECTORY_COLOR))
        self.setPen(QPen(Qt.GlobalColor.transparent, 0))
        self.setAcceptHoverEvents(True)
        self.setZValue(15)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        for idx in self._neighbor_indices:
            if 0 <= idx < len(self._radio_map_items):
                item = self._radio_map_items[idx]
                item.setBrush(QBrush(FP_RADIO_MAP_HIGHLIGHT))
                r = FP_RADIO_MAP_RADIUS * 1.8
                item.setRect(-r, -r, 2 * r, 2 * r)
                item.setZValue(16)
        if self._estimated_pos_item is not None:
            self._estimated_pos_item.setVisible(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        for idx in self._neighbor_indices:
            if 0 <= idx < len(self._radio_map_items):
                item = self._radio_map_items[idx]
                item.setBrush(QBrush(FP_RADIO_MAP_COLOR))
                r = FP_RADIO_MAP_RADIUS
                item.setRect(-r, -r, 2 * r, 2 * r)
                item.setZValue(12)
        if self._estimated_pos_item is not None:
            self._estimated_pos_item.setVisible(False)
        super().hoverLeaveEvent(event)


class BeaconGraphicsItem(QGraphicsEllipseItem):
    def __init__(
        self, beacon: Beacon, index: int, canvas: FloorPlanCanvas | None = None
    ):
        r = BEACON_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.beacon = beacon
        self.beacon_index = index
        self._canvas = canvas
        self.setPos(beacon.x, beacon.y)
        self.setBrush(QBrush(BEACON_COLOR))
        self.setPen(QPen(Qt.GlobalColor.black, 1))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10)

        label = QGraphicsSimpleTextItem(beacon.label or f"B-{index + 1}", self)
        font = QFont()
        font.setPixelSize(max(8, int(r * 1.8)))
        font.setBold(True)
        label.setFont(font)
        label.setPos(r + 2, -r - 2)
        label.setBrush(QBrush(QColor("#b71c1c")))

    def set_interaction_enabled(self, enabled: bool) -> None:
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, enabled)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, enabled)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            self.beacon.x = pos.x()
            self.beacon.y = pos.y()
            if self._canvas is not None:
                mx, my = self._canvas.scene_to_meters(pos)
                self._canvas.beacon_moved.emit(self.beacon_index, mx, my)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        if self._canvas is not None:
            pos = self.pos()
            mx, my = self._canvas.scene_to_meters(pos)
            self._canvas.beacon_move_finished.emit(self.beacon_index, mx, my)
        super().mouseReleaseEvent(event)


class FloorPlanCanvas(QGraphicsView):
    beacon_placed = Signal(float, float)
    waypoint_placed = Signal(float, float)
    beacon_moved = Signal(int, float, float)
    beacon_move_finished = Signal(int, float, float)
    wall_drawn = Signal(float, float, float, float)
    door_drawn = Signal(float, float, float, float)
    room_drawn = Signal(float, float, float, float)
    walls_deleted = Signal(list)
    doors_deleted = Signal(list)

    beacon_deleted = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self._floor_plan_item: QGraphicsPixmapItem | None = None
        self._tool_mode: ToolMode = ToolMode.SELECT
        self._beacon_items: list[BeaconGraphicsItem] = []
        self._wall_items: list[QGraphicsLineItem] = []
        self._wall_models: list[Wall] = []
        self._selection_rect: QGraphicsRectItem | None = None
        self._selection_start: QPointF | None = None
        self._selected_wall_indices: list[int] = []
        self._selected_door_indices: list[int] = []
        self._selected_beacon_items: list[BeaconGraphicsItem] = []
        self._door_models: list[Door] = []

        self._door_items: list[QGraphicsLineItem] = []
        self._trajectory_items: list[QGraphicsItem] = []
        self._waypoint_items: list[QGraphicsEllipseItem] = []
        self._estimated_trajectory_items: dict[str, list[QGraphicsItem]] = {}
        self._grid_items: list[QGraphicsItem] = []
        self._heatmap_item: QGraphicsPixmapItem | None = None
        self._fingerprint_overlay_items: list[QGraphicsItem] = []

        self._line_start: QPointF | None = None
        self._temp_line: QGraphicsLineItem | None = None
        self._snap_cursor: QGraphicsEllipseItem | None = None
        self._drawing_chain: bool = False

        self._room_start: QPointF | None = None
        self._temp_rect: QGraphicsRectItem | None = None

        self._scale_x: float = 1.0
        self._scale_y: float = 1.0
        self._dimensions: tuple[float, float] = (50.0, 50.0)
        self._has_floor_plan: bool = False
        self._floor_plan_visible: bool = True
        self._middle_pan_active: bool = False
        self._middle_pan_last_pos: QPoint | None = None

        self._snap_enabled: bool = True
        self._snap_spacing: float = 0.5
        self._wall_color: QColor = QColor(WALL_COLOR)

        self.set_dimensions((50.0, 50.0))

    # ── Properties ──

    @property
    def tool_mode(self) -> ToolMode:
        return self._tool_mode

    @property
    def snap_enabled(self) -> bool:
        return self._snap_enabled

    @property
    def snap_spacing(self) -> float:
        return self._snap_spacing

    def clear_selection(self) -> None:
        if self._selection_rect is not None:
            self._scene.removeItem(self._selection_rect)
            self._selection_rect = None

        for idx in self._selected_wall_indices:
            if idx < len(self._wall_items):
                self._wall_items[idx].setPen(QPen(self._wall_color, 3))

        for idx in self._selected_door_indices:
            if idx < len(self._door_items):
                self._door_items[idx].setPen(QPen(DOOR_COLOR, 4, Qt.PenStyle.DashLine))

        for item in self._selected_beacon_items:
            item.setBrush(QBrush(BEACON_COLOR))

        self._selected_wall_indices.clear()
        self._selected_door_indices.clear()
        self._selected_beacon_items.clear()
        self._selection_start = None

    def _select_item_at(self, scene_pos: QPointF) -> bool:
        self.clear_selection()

        for item in self._scene.items(scene_pos):
            if isinstance(item, BeaconGraphicsItem):
                self._selected_beacon_items.append(item)
                item.setBrush(QBrush(BEACON_SELECTED_COLOR))
                return True

            if item in self._door_items:
                idx = self._door_items.index(item)
                self._selected_door_indices.append(idx)
                item.setPen(QPen(QColor("#f39c12"), 5, Qt.PenStyle.DashLine))
                return True

            if item in self._wall_items:
                idx = self._wall_items.index(item)
                self._selected_wall_indices.append(idx)
                item.setPen(QPen(QColor("#f39c12"), 5))
                return True

        return False

    def set_tool_mode(self, mode: ToolMode) -> None:
        self.clear_selection()
        self._cancel_drawing()
        self._tool_mode = mode
        if mode == ToolMode.PAN:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        beacon_interaction_enabled = mode == ToolMode.SELECT
        for item in self._beacon_items:
            item.set_interaction_enabled(beacon_interaction_enabled)
        self._update_snap_cursor_visibility()

    def set_snap_enabled(self, enabled: bool) -> None:
        self._snap_enabled = enabled
        if not self._has_floor_plan:
            self._draw_grid()
        self._update_snap_cursor_visibility()

    def set_snap_spacing(self, spacing: float) -> None:
        self._snap_spacing = max(0.1, spacing)

    def set_wall_color(self, color: QColor) -> None:
        self._wall_color = QColor(color)
        for i, item in enumerate(self._wall_items):
            if i in self._selected_wall_indices:
                item.setPen(QPen(QColor("#f39c12"), 5))
            else:
                item.setPen(QPen(self._wall_color, 3))

    @property
    def wall_color(self) -> QColor:
        return self._wall_color

    # ── Snap logic ──

    def _snap_scene_pos(self, scene_pos: QPointF) -> QPointF:
        if not self._snap_enabled or self._snap_spacing <= 0:
            return scene_pos
        mx, my = self.scene_to_meters(scene_pos)
        s = self._snap_spacing
        snapped_mx = round(mx / s) * s
        snapped_my = round(my / s) * s
        snapped_mx = max(0.0, min(snapped_mx, self._dimensions[0]))
        snapped_my = max(0.0, min(snapped_my, self._dimensions[1]))
        return self.meters_to_scene(snapped_mx, snapped_my)

    def _update_snap_cursor_visibility(self) -> None:
        is_drawing_tool = self._tool_mode in (
            ToolMode.DRAW_WALL,
            ToolMode.DRAW_DOOR,
            ToolMode.PLACE_BEACON,
            ToolMode.DRAW_TRAJECTORY,
            ToolMode.DRAW_ROOM,
        )
        if self._snap_cursor and not (self._snap_enabled and is_drawing_tool):
            self._scene.removeItem(self._snap_cursor)
            self._snap_cursor = None

    def _update_snap_cursor(self, scene_pos: QPointF) -> None:
        is_drawing_tool = self._tool_mode in (
            ToolMode.DRAW_WALL,
            ToolMode.DRAW_DOOR,
            ToolMode.PLACE_BEACON,
            ToolMode.DRAW_TRAJECTORY,
            ToolMode.DRAW_ROOM,
        )
        if not self._snap_enabled or not is_drawing_tool:
            if self._snap_cursor:
                self._scene.removeItem(self._snap_cursor)
                self._snap_cursor = None
            return

        snapped = self._snap_scene_pos(scene_pos)
        r = max(1.0, 0.8 / self._scale_x)

        if self._snap_cursor is None:
            pen = QPen(SNAP_CURSOR_COLOR, 0.8)
            brush = QBrush(QColor(255, 80, 80, 40))
            self._snap_cursor = self._scene.addEllipse(-r, -r, 2 * r, 2 * r, pen, brush)
            self._snap_cursor.setZValue(100)
        self._snap_cursor.setPos(snapped)

    # ── Cancel in-progress drawing ──

    def _cancel_drawing(self) -> None:
        if self._temp_line:
            self._scene.removeItem(self._temp_line)
            self._temp_line = None
        self._line_start = None
        self._drawing_chain = False
        if self._temp_rect:
            self._scene.removeItem(self._temp_rect)
            self._temp_rect = None
        self._room_start = None

    # ── Dimensions / Grid ──

    def set_dimensions(self, dimensions: tuple[float, float]) -> None:
        self._dimensions = dimensions
        if not self._has_floor_plan:
            w_px = dimensions[0] * PIXELS_PER_METER
            h_px = dimensions[1] * PIXELS_PER_METER
            self._scale_x = dimensions[0] / w_px
            self._scale_y = dimensions[1] / h_px
            self._scene.setSceneRect(0, 0, w_px, h_px)
            self._draw_grid()
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _draw_grid(self) -> None:
        for item in self._grid_items:
            self._scene.removeItem(item)
        self._grid_items.clear()

        if self._has_floor_plan:
            scene_rect = self._scene.sceneRect()
            w_px = scene_rect.width()
            h_px = scene_rect.height()
        else:
            w_px = self._dimensions[0] * PIXELS_PER_METER
            h_px = self._dimensions[1] * PIXELS_PER_METER

        bg = self._scene.addRect(
            0, 0, w_px, h_px, QPen(Qt.PenStyle.NoPen), QBrush(BG_COLOR)
        )
        bg.setZValue(-2)
        self._grid_items.append(bg)

        border_pen = QPen(QColor(180, 180, 180), 1.5)
        border = self._scene.addRect(0, 0, w_px, h_px, border_pen)
        border.setZValue(-1)
        self._grid_items.append(border)

        grid_spacing_m = self._pick_grid_spacing()
        grid_pen = QPen(GRID_COLOR, 0.5)
        label_font = QFont()
        label_font.setPointSize(6)

        x_m = 0.0
        while x_m <= self._dimensions[0]:
            x_px = x_m / self._scale_x
            if not self._snap_enabled:
                line = self._scene.addLine(x_px, 0, x_px, h_px, grid_pen)
                line.setZValue(-1)
                self._grid_items.append(line)

            label = self._scene.addText(f"{x_m:.0f}", label_font)
            label.setDefaultTextColor(GRID_LABEL_COLOR)
            label.setPos(x_px + 1, 1)
            label.setZValue(-1)
            self._grid_items.append(label)

            x_m += grid_spacing_m

        y_m = 0.0
        while y_m <= self._dimensions[1]:
            y_px = y_m / self._scale_y
            if not self._snap_enabled:
                line = self._scene.addLine(0, y_px, w_px, y_px, grid_pen)
                line.setZValue(-1)
                self._grid_items.append(line)

            if y_m > 0:
                label = self._scene.addText(f"{y_m:.0f}", label_font)
                label.setDefaultTextColor(GRID_LABEL_COLOR)
                label.setPos(1, y_px + 1)
                label.setZValue(-1)
                self._grid_items.append(label)

            y_m += grid_spacing_m

    def _pick_grid_spacing(self) -> float:
        max_dim = max(self._dimensions)
        if max_dim <= 10:
            return 2.0
        if max_dim <= 25:
            return 5.0
        if max_dim <= 60:
            return 10.0
        if max_dim <= 150:
            return 20.0
        return 50.0

    # ── Floor plan image ──

    def load_floor_plan(
        self, image_path: str, dimensions: tuple[float, float] = (50.0, 50.0)
    ) -> None:
        if self._floor_plan_item:
            self._scene.removeItem(self._floor_plan_item)
            self._floor_plan_item = None

        for item in self._grid_items:
            self._scene.removeItem(item)
        self._grid_items.clear()

        pixmap = QPixmap(resolve_floor_plan_image_path(image_path))
        if pixmap.isNull():
            self._has_floor_plan = False
            self.set_dimensions(dimensions)
            return

        self._has_floor_plan = True
        self._floor_plan_item = QGraphicsPixmapItem(pixmap)
        self._floor_plan_item.setZValue(-1)
        self._floor_plan_item.setVisible(self._floor_plan_visible)
        self._scene.addItem(self._floor_plan_item)

        self._dimensions = dimensions
        self._scale_x = dimensions[0] / pixmap.width()
        self._scale_y = dimensions[1] / pixmap.height()

        self._floor_plan_item.setScale(1.0)
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_floor_plan_visible(self, visible: bool) -> None:
        self._floor_plan_visible = visible
        if self._floor_plan_item is not None:
            self._floor_plan_item.setVisible(visible)
        if visible:
            for item in self._grid_items:
                self._scene.removeItem(item)
            self._grid_items.clear()
        elif self._has_floor_plan:
            self._draw_grid()

    # ── Coordinate conversion ──

    def scene_to_meters(self, scene_pos: QPointF) -> tuple[float, float]:
        return (scene_pos.x() * self._scale_x, scene_pos.y() * self._scale_y)

    def meters_to_scene(self, mx: float, my: float) -> QPointF:
        return QPointF(mx / self._scale_x, my / self._scale_y)

    # ── Add items ──

    def add_beacon(self, beacon: Beacon, index: int) -> None:
        scene_pos = self.meters_to_scene(beacon.x, beacon.y)
        beacon_copy = Beacon(
            x=beacon.x,
            y=beacon.y,
            z=beacon.z,
            label=beacon.label,
            frequency=beacon.frequency,
            level_index=beacon.level_index,
        )
        item = BeaconGraphicsItem(beacon_copy, index, canvas=self)
        item.set_interaction_enabled(self._tool_mode == ToolMode.SELECT)
        item.setPos(scene_pos)
        self._scene.addItem(item)
        self._beacon_items.append(item)

    def clear_beacons(self) -> None:
        for item in self._beacon_items:
            self._scene.removeItem(item)
        self._beacon_items.clear()

    def add_wall(self, wall: Wall) -> None:
        p1 = self.meters_to_scene(wall.start.x, wall.start.y)
        p2 = self.meters_to_scene(wall.end.x, wall.end.y)
        pen = QPen(self._wall_color, 3)
        line = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        line.setZValue(5)
        self._wall_items.append(line)
        self._wall_models.append(wall)

    def remove_last_wall(self) -> None:
        if self._wall_items:
            self._scene.removeItem(self._wall_items.pop())
        if self._wall_models:
            self._wall_models.pop()

    def add_door(self, door: Door) -> None:
        p1 = self.meters_to_scene(door.start.x, door.start.y)
        p2 = self.meters_to_scene(door.end.x, door.end.y)
        pen = QPen(DOOR_COLOR, 4, Qt.PenStyle.DashLine)
        line = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
        line.setZValue(6)
        self._door_items.append(line)
        self._door_models.append(door)

    def remove_last_door(self) -> None:
        if self._door_items:
            self._scene.removeItem(self._door_items.pop())
            self._door_models.pop()

    DOOR_LENGTH = 0.7
    DOOR_SNAP_TOLERANCE_M = 1.5

    def place_door_on_wall(self, click_mx: float, click_my: float) -> Door | None:
        """Find nearest wall to click point and create a 0.7m door centered on it."""
        best_wall: Wall | None = None
        best_dist = float("inf")
        best_t = 0.0

        for wall in self._wall_models:
            ax, ay = wall.start.x, wall.start.y
            bx, by = wall.end.x, wall.end.y
            dx, dy = bx - ax, by - ay
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-12:
                continue

            t = ((click_mx - ax) * dx + (click_my - ay) * dy) / seg_len_sq
            t = max(0.0, min(1.0, t))

            proj_x = ax + t * dx
            proj_y = ay + t * dy
            dist = math.hypot(click_mx - proj_x, click_my - proj_y)

            if dist < best_dist:
                best_dist = dist
                best_wall = wall
                best_t = t

        if best_wall is None or best_dist > self.DOOR_SNAP_TOLERANCE_M:
            return None

        ax, ay = best_wall.start.x, best_wall.start.y
        bx, by = best_wall.end.x, best_wall.end.y
        wall_dx, wall_dy = bx - ax, by - ay
        wall_len = math.hypot(wall_dx, wall_dy)
        if wall_len < 1e-12:
            return None

        ux, uy = wall_dx / wall_len, wall_dy / wall_len

        center_along = best_t * wall_len
        half_door = self.DOOR_LENGTH / 2.0
        center_along = max(half_door, min(center_along, wall_len - half_door))

        door_start_along = center_along - half_door
        door_end_along = center_along + half_door

        sx = ax + ux * door_start_along
        sy = ay + uy * door_start_along
        ex = ax + ux * door_end_along
        ey = ay + uy * door_end_along

        return Door(start=Node(x=sx, y=sy), end=Node(x=ex, y=ey))

    def add_waypoint(self, mx: float, my: float) -> None:
        sp = self.meters_to_scene(mx, my)
        r = 4
        item = self._scene.addEllipse(
            sp.x() - r,
            sp.y() - r,
            2 * r,
            2 * r,
            QPen(WAYPOINT_COLOR, 1),
            QBrush(WAYPOINT_COLOR),
        )
        item.setZValue(8)
        self._waypoint_items.append(item)

        if len(self._waypoint_items) > 1:
            prev = self._waypoint_items[-2]
            prev_center = prev.rect().center()
            pen = QPen(WAYPOINT_COLOR, 1.5, Qt.PenStyle.DashLine)
            line = self._scene.addLine(
                prev_center.x(), prev_center.y(), sp.x(), sp.y(), pen
            )
            line.setZValue(7)
            self._trajectory_items.append(line)

    def clear_waypoints(self) -> None:
        for item in self._waypoint_items:
            self._scene.removeItem(item)
        self._waypoint_items.clear()
        for item in self._trajectory_items:
            self._scene.removeItem(item)
        self._trajectory_items.clear()

    # ── Trajectory drawing ──

    def draw_real_trajectory(self, points: list[TrajectoryPoint]) -> None:
        self._clear_trajectory_path("__real__")
        if len(points) < 2:
            return

        path = QPainterPath()
        sp0 = self.meters_to_scene(points[0].x, points[0].y)
        path.moveTo(sp0)

        for p in points[1:]:
            sp = self.meters_to_scene(p.x, p.y)
            path.lineTo(sp)

        pen = QPen(TRAJECTORY_REAL_COLOR, 2)
        path_item = self._scene.addPath(path, pen)
        path_item.setZValue(6)
        self._estimated_trajectory_items["__real__"] = [path_item]

    def draw_estimated_trajectory(
        self,
        name: str,
        points: list[TrajectoryPoint],
        color: QColor | None = None,
    ) -> None:
        self._clear_trajectory_path(name)
        if len(points) < 2:
            return

        if color is None:
            color = TRAJECTORY_ESTIMATED_COLOR

        path = QPainterPath()
        sp0 = self.meters_to_scene(points[0].x, points[0].y)
        path.moveTo(sp0)

        for p in points[1:]:
            sp = self.meters_to_scene(p.x, p.y)
            path.lineTo(sp)

        pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
        path_item = self._scene.addPath(path, pen)
        path_item.setZValue(7)
        self._estimated_trajectory_items[name] = [path_item]

    def _clear_trajectory_path(self, name: str) -> None:
        if name in self._estimated_trajectory_items:
            for item in self._estimated_trajectory_items[name]:
                self._scene.removeItem(item)
            del self._estimated_trajectory_items[name]

    def clear_all_trajectories(self) -> None:
        for name in list(self._estimated_trajectory_items.keys()):
            self._clear_trajectory_path(name)

    def clear_all(self) -> None:
        self.clear_beacons()
        self.clear_waypoints()
        self.clear_all_trajectories()
        self.remove_heatmap_overlay()
        self.clear_fingerprint_overlay()
        for item in self._wall_items:
            self._scene.removeItem(item)
        self._wall_items.clear()
        self._wall_models.clear()
        for item in self._door_items:
            self._scene.removeItem(item)
        self._door_items.clear()
        self._door_models.clear()

    def clear_floor_plan(self) -> None:
        if self._floor_plan_item:
            self._scene.removeItem(self._floor_plan_item)
            self._floor_plan_item = None
        self._has_floor_plan = False

    # ── Heatmap overlay ──

    def set_heatmap_overlay(
        self,
        grid: np.ndarray,
        x_edges: np.ndarray,
        y_edges: np.ndarray,
        opacity: float = 0.55,
    ) -> None:
        self.remove_heatmap_overlay()

        vmin, vmax = float(np.nanmin(grid)), float(np.nanmax(grid))
        if vmax - vmin < 1e-12:
            return

        norm = (grid - vmin) / (vmax - vmin)

        ny, nx = norm.shape
        img = QImage(nx, ny, QImage.Format.Format_ARGB32)
        for iy in range(ny):
            for ix in range(nx):
                v = norm[iy, ix]
                r, g, b = _viridis_rgb(v)
                img.setPixelColor(ix, iy, QColor(r, g, b, 220))

        top_left = self.meters_to_scene(float(x_edges[0]), float(y_edges[0]))
        bottom_right = self.meters_to_scene(float(x_edges[-1]), float(y_edges[-1]))
        target_w = bottom_right.x() - top_left.x()
        target_h = bottom_right.y() - top_left.y()

        if target_w < 1 or target_h < 1:
            return

        scaled = img.scaled(
            int(target_w),
            int(target_h),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        pixmap = QPixmap.fromImage(scaled)
        self._heatmap_item = QGraphicsPixmapItem(pixmap)
        self._heatmap_item.setPos(top_left)
        self._heatmap_item.setOpacity(opacity)
        self._heatmap_item.setZValue(3)
        self._scene.addItem(self._heatmap_item)

    def remove_heatmap_overlay(self) -> None:
        if self._heatmap_item is not None:
            self._scene.removeItem(self._heatmap_item)
            self._heatmap_item = None

    # ── Fingerprint overlay (for F5) ──

    def clear_fingerprint_overlay(self) -> None:
        for item in self._fingerprint_overlay_items:
            self._scene.removeItem(item)
        self._fingerprint_overlay_items.clear()

    def show_fingerprint_overlay(
        self,
        radio_map_positions: np.ndarray,
        trajectory_points: list[TrajectoryPoint],
        estimated_points: list[TrajectoryPoint],
        neighbor_indices: list[np.ndarray],
    ) -> None:
        self.clear_fingerprint_overlay()

        radio_map_items: list[QGraphicsEllipseItem] = []
        r = FP_RADIO_MAP_RADIUS
        pen = QPen(Qt.GlobalColor.transparent, 0)
        brush = QBrush(FP_RADIO_MAP_COLOR)

        for pos in radio_map_positions:
            sp = self.meters_to_scene(float(pos[0]), float(pos[1]))
            dot = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
            dot.setPos(sp)
            dot.setBrush(brush)
            dot.setPen(pen)
            dot.setZValue(12)
            self._scene.addItem(dot)
            radio_map_items.append(dot)
            self._fingerprint_overlay_items.append(dot)

        for i, (traj_pt, est_pt) in enumerate(zip(trajectory_points, estimated_points)):
            est_sp = self.meters_to_scene(est_pt.x, est_pt.y)
            er = FP_TRAJECTORY_RADIUS * 0.8
            est_dot = QGraphicsEllipseItem(-er, -er, 2 * er, 2 * er)
            est_dot.setPos(est_sp)
            est_dot.setBrush(QBrush(FP_ESTIMATED_COLOR))
            est_dot.setPen(QPen(Qt.GlobalColor.transparent, 0))
            est_dot.setZValue(14)
            est_dot.setVisible(False)
            self._scene.addItem(est_dot)
            self._fingerprint_overlay_items.append(est_dot)

            nidx = neighbor_indices[i] if i < len(neighbor_indices) else np.array([])
            traj_sp = self.meters_to_scene(traj_pt.x, traj_pt.y)
            dot = _FpTrajectoryDot(
                point_index=i,
                neighbor_indices=nidx,
                radio_map_items=radio_map_items,
                estimated_pos_item=est_dot,
            )
            dot.setPos(traj_sp)
            self._scene.addItem(dot)
            self._fingerprint_overlay_items.append(dot)

    # ── Events ──

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1.0 / factor, 1.0 / factor)

    def mousePressEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.pos())

        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_pan_active = True
            self._middle_pan_last_pos = event.pos()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._tool_mode == ToolMode.SELECT:
                if self._select_item_at(scene_pos):
                    return

            if self._tool_mode == ToolMode.RECT_SELECT:
                self.clear_selection()
                self._selection_start = self.mapToScene(event.pos())
                self._selection_rect = self._scene.addRect(
                    QRectF(self._selection_start, self._selection_start),
                    QPen(QColor(52, 152, 219, 180), 1),
                    QBrush(QColor(52, 152, 219, 40)),
                )
                self._selection_rect.setZValue(50)
                return

            if self._snap_enabled:
                scene_pos = self._snap_scene_pos(scene_pos)

            if self._tool_mode == ToolMode.PLACE_BEACON:
                mx, my = self.scene_to_meters(scene_pos)
                self.beacon_placed.emit(mx, my)
                return

            if self._tool_mode == ToolMode.DRAW_TRAJECTORY:
                mx, my = self.scene_to_meters(scene_pos)
                self.waypoint_placed.emit(mx, my)
                return

            if self._tool_mode == ToolMode.DRAW_DOOR:
                mx, my = self.scene_to_meters(scene_pos)
                door = self.place_door_on_wall(mx, my)
                if door is not None:
                    self.door_drawn.emit(
                        door.start.x, door.start.y, door.end.x, door.end.y
                    )
                return

            if self._tool_mode == ToolMode.DRAW_WALL:
                if self._line_start is None:
                    self._line_start = scene_pos
                    self._drawing_chain = True
                    pen = QPen(self._wall_color, 2, Qt.PenStyle.DashLine)
                    self._temp_line = self._scene.addLine(
                        scene_pos.x(),
                        scene_pos.y(),
                        scene_pos.x(),
                        scene_pos.y(),
                        pen,
                    )
                else:
                    mx1, my1 = self.scene_to_meters(self._line_start)
                    mx2, my2 = self.scene_to_meters(scene_pos)

                    if self._temp_line:
                        self._scene.removeItem(self._temp_line)
                        self._temp_line = None

                    self.wall_drawn.emit(mx1, my1, mx2, my2)

                    self._line_start = scene_pos
                    pen = QPen(self._wall_color, 2, Qt.PenStyle.DashLine)
                    self._temp_line = self._scene.addLine(
                        scene_pos.x(),
                        scene_pos.y(),
                        scene_pos.x(),
                        scene_pos.y(),
                        pen,
                    )
                return

            if self._tool_mode == ToolMode.DRAW_ROOM:
                self._room_start = scene_pos
                pen = QPen(self._wall_color, 2, Qt.PenStyle.DashLine)
                self._temp_rect = self._scene.addRect(
                    QRectF(scene_pos, scene_pos),
                    pen,
                    QBrush(QColor(0, 0, 0, 0)),
                )
                return

        if event.button() == Qt.MouseButton.RightButton:
            if self._drawing_chain:
                self._cancel_drawing()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._middle_pan_active:
            if self._middle_pan_last_pos is not None:
                delta = event.pos() - self._middle_pan_last_pos
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x()
                )
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y()
                )
                self._middle_pan_last_pos = event.pos()
            return

        scene_pos = self.mapToScene(event.pos())

        if (
            self._tool_mode == ToolMode.RECT_SELECT
            and self._selection_start is not None
            and self._selection_rect is not None
        ):
            self._selection_rect.setRect(
                QRectF(self._selection_start, scene_pos).normalized()
            )
            return

        self._update_snap_cursor(scene_pos)

        if self._temp_line and self._line_start:
            if self._snap_enabled:
                scene_pos = self._snap_scene_pos(scene_pos)
            self._temp_line.setLine(
                self._line_start.x(),
                self._line_start.y(),
                scene_pos.x(),
                scene_pos.y(),
            )

        if self._temp_rect and self._room_start:
            if self._snap_enabled:
                scene_pos = self._snap_scene_pos(scene_pos)
            self._temp_rect.setRect(QRectF(self._room_start, scene_pos).normalized())

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_pan_active:
            self._middle_pan_active = False
            self._middle_pan_last_pos = None
            self.viewport().unsetCursor()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._tool_mode == ToolMode.RECT_SELECT:
                if self._selection_rect is not None:
                    rect = self._selection_rect.rect()

                    for i, w_item in enumerate(self._wall_items):
                        if rect.intersects(w_item.boundingRect()):
                            self._selected_wall_indices.append(i)
                            w_item.setPen(QPen(QColor("#f39c12"), 5))

                    for i, d_item in enumerate(self._door_items):
                        if rect.intersects(d_item.boundingRect()):
                            self._selected_door_indices.append(i)
                            d_item.setPen(
                                QPen(QColor("#f39c12"), 5, Qt.PenStyle.DashLine)
                            )

                    for b_item in self._beacon_items:
                        if rect.contains(b_item.pos()):
                            self._selected_beacon_items.append(b_item)
                            b_item.setBrush(QBrush(BEACON_SELECTED_COLOR))

                    self._scene.removeItem(self._selection_rect)
                    self._selection_rect = None
                return

            if self._tool_mode == ToolMode.DRAW_ROOM:
                if self._temp_rect and self._room_start:
                    scene_pos = self.mapToScene(event.pos())
                    if self._snap_enabled:
                        scene_pos = self._snap_scene_pos(scene_pos)
                    self._scene.removeItem(self._temp_rect)
                    self._temp_rect = None

                    mx1, my1 = self.scene_to_meters(self._room_start)
                    mx2, my2 = self.scene_to_meters(scene_pos)
                    self._room_start = None

                    if abs(mx2 - mx1) > 0.01 and abs(my2 - my1) > 0.01:
                        self.room_drawn.emit(mx1, my1, mx2, my2)
                return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._drawing_chain:
                self._cancel_drawing()
                return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if (
                self._selected_wall_indices
                or self._selected_door_indices
                or self._selected_beacon_items
            ):
                deleted_walls = []
                for i in sorted(self._selected_wall_indices, reverse=True):
                    deleted_walls.append(self._wall_models[i])
                    self._scene.removeItem(self._wall_items[i])
                    self._wall_items.pop(i)
                    self._wall_models.pop(i)
                if deleted_walls:
                    self.walls_deleted.emit(deleted_walls)

                deleted_doors = []
                for i in sorted(self._selected_door_indices, reverse=True):
                    deleted_doors.append(self._door_models[i])
                    self._scene.removeItem(self._door_items[i])
                    self._door_items.pop(i)
                    self._door_models.pop(i)
                if deleted_doors:
                    self.doors_deleted.emit(deleted_doors)

                for b_item in self._selected_beacon_items:
                    self.beacon_deleted.emit(b_item.beacon_index)
                    self._scene.removeItem(b_item)
                    if b_item in self._beacon_items:
                        self._beacon_items.remove(b_item)

                self.clear_selection()
                return

            # Keep existing logic for normal selection

            for item in self._scene.selectedItems():
                if isinstance(item, BeaconGraphicsItem):
                    self.beacon_deleted.emit(item.beacon_index)
                    self._scene.removeItem(item)
                    if item in self._beacon_items:
                        self._beacon_items.remove(item)
            return
        super().keyPressEvent(event)
