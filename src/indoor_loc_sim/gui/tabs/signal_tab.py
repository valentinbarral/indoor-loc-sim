from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal as QSignal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QComboBox,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QSplitter,
    QCheckBox,
    QStackedWidget,
    QScrollArea,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from indoor_loc_sim.engine.signals import (
    BeaconSignal,
    HeatmapResult,
    NlosMode,
    SignalType,
    generate_beacon_signal,
    generate_rss_heatmap,
)
from indoor_loc_sim.gui.state import AppState
from indoor_loc_sim.gui.widgets.floor_plan_canvas import FloorPlanCanvas


class HeatmapWorker(QThread):
    finished = QSignal(object)
    error = QSignal(str)

    def __init__(self, **kwargs):
        super().__init__()
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = generate_rss_heatmap(**self._kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class SignalGenerationTab(QWidget):
    def __init__(
        self, state: AppState, planimetry_canvas: FloorPlanCanvas, parent=None
    ):
        super().__init__(parent)
        self._state = state
        self._planimetry_canvas = planimetry_canvas
        self._heatmap_worker: HeatmapWorker | None = None
        self._building_ui_dirty = True
        self._setup_ui()
        self._connect_signals()
        self.sync_from_state()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        config_group = QGroupBox("Signal Configuration")
        config_layout = QVBoxLayout(config_group)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Signal type:"))
        self._combo_type = QComboBox()
        self._combo_type.addItems(["RSS", "ToF"])
        type_layout.addWidget(self._combo_type)
        config_layout.addLayout(type_layout)

        samples_layout = QHBoxLayout()
        samples_layout.addWidget(QLabel("Samples per point:"))
        self._spin_samples = QSpinBox()
        self._spin_samples.setRange(1, 100)
        self._spin_samples.setValue(1)
        self._spin_samples.setToolTip(
            "Number of independent measurements averaged at each trajectory point"
        )
        samples_layout.addWidget(self._spin_samples)
        config_layout.addLayout(samples_layout)

        noise_group = QGroupBox("Noise Parameters")
        noise_layout = QVBoxLayout(noise_group)

        rss_noise_layout = QHBoxLayout()
        rss_noise_layout.addWidget(QLabel("RSS σ:"))
        self._spin_rss_sigma = QDoubleSpinBox()
        self._spin_rss_sigma.setRange(0.0, 20.0)
        self._spin_rss_sigma.setValue(2.0)
        self._spin_rss_sigma.setSingleStep(0.1)
        self._spin_rss_sigma.setDecimals(1)
        rss_noise_layout.addWidget(self._spin_rss_sigma)
        noise_layout.addLayout(rss_noise_layout)

        tof_noise_layout = QHBoxLayout()
        tof_noise_layout.addWidget(QLabel("ToF σ (ns):"))
        self._spin_tof_sigma = QDoubleSpinBox()
        self._spin_tof_sigma.setRange(0.0, 1000.0)
        self._spin_tof_sigma.setValue(1.0)
        self._spin_tof_sigma.setSingleStep(0.1)
        self._spin_tof_sigma.setDecimals(1)
        tof_noise_layout.addWidget(self._spin_tof_sigma)
        noise_layout.addLayout(tof_noise_layout)

        config_layout.addWidget(noise_group)

        propagation_group = QGroupBox("Propagation Model")
        propagation_layout = QVBoxLayout(propagation_group)

        rssi_ref_layout = QHBoxLayout()
        rssi_ref_layout.addWidget(QLabel("A (RSSI at d₀):"))
        self._spin_rssi_at_ref = QDoubleSpinBox()
        self._spin_rssi_at_ref.setRange(-120.0, 0.0)
        self._spin_rssi_at_ref.setValue(-59.0)
        self._spin_rssi_at_ref.setSingleStep(1.0)
        self._spin_rssi_at_ref.setDecimals(1)
        self._spin_rssi_at_ref.setSuffix(" dBm")
        rssi_ref_layout.addWidget(self._spin_rssi_at_ref)
        propagation_layout.addLayout(rssi_ref_layout)

        d0_layout = QHBoxLayout()
        d0_layout.addWidget(QLabel("d₀ (ref. distance):"))
        self._spin_d0 = QDoubleSpinBox()
        self._spin_d0.setRange(0.1, 10.0)
        self._spin_d0.setValue(1.0)
        self._spin_d0.setSingleStep(0.1)
        self._spin_d0.setDecimals(1)
        self._spin_d0.setSuffix(" m")
        d0_layout.addWidget(self._spin_d0)
        propagation_layout.addLayout(d0_layout)

        att_layout = QHBoxLayout()
        att_layout.addWidget(QLabel("Wall attenuation (dB):"))
        self._spin_wall_att = QDoubleSpinBox()
        self._spin_wall_att.setRange(0.0, 30.0)
        self._spin_wall_att.setValue(3.0)
        self._spin_wall_att.setSingleStep(0.5)
        self._spin_wall_att.setDecimals(1)
        att_layout.addWidget(self._spin_wall_att)
        propagation_layout.addLayout(att_layout)

        nlos_layout = QHBoxLayout()
        nlos_layout.addWidget(QLabel("NLoS mode (ToF):"))
        self._combo_nlos = QComboBox()
        self._combo_nlos.addItems(["None", "Increase error", "Skip measurement"])
        nlos_layout.addWidget(self._combo_nlos)
        propagation_layout.addLayout(nlos_layout)

        nlos_mult_layout = QHBoxLayout()
        nlos_mult_layout.addWidget(QLabel("NLoS error multiplier:"))
        self._spin_nlos_mult = QDoubleSpinBox()
        self._spin_nlos_mult.setRange(1.0, 100.0)
        self._spin_nlos_mult.setValue(10.0)
        self._spin_nlos_mult.setSingleStep(1.0)
        nlos_mult_layout.addWidget(self._spin_nlos_mult)
        propagation_layout.addLayout(nlos_mult_layout)

        ple_layout = QHBoxLayout()
        ple_layout.addWidget(QLabel("Path loss exponent:"))
        self._spin_path_loss = QDoubleSpinBox()
        self._spin_path_loss.setRange(1.0, 6.0)
        self._spin_path_loss.setValue(2.0)
        self._spin_path_loss.setSingleStep(0.1)
        self._spin_path_loss.setDecimals(1)
        self._spin_path_loss.setToolTip(
            "Free-space ≈ 2.0, indoor LoS ≈ 1.6–1.8, indoor NLoS ≈ 2.7–5.0"
        )
        ple_layout.addWidget(self._spin_path_loss)
        propagation_layout.addLayout(ple_layout)

        config_layout.addWidget(propagation_group)
        left_layout.addWidget(config_group)

        self._btn_generate = QPushButton("Generate Signals")
        self._btn_generate.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 8px; }"
        )
        left_layout.addWidget(self._btn_generate)

        info_group = QGroupBox("Signal Info")
        info_layout = QVBoxLayout(info_group)
        self._lbl_info = QLabel("No signals generated")
        self._lbl_info.setWordWrap(True)
        info_layout.addWidget(self._lbl_info)
        left_layout.addWidget(info_group)

        beacon_vis_group = QGroupBox("Beacon Visibility")
        beacon_vis_layout = QVBoxLayout(beacon_vis_group)
        self._beacon_checkboxes: list[QCheckBox] = []
        self._beacon_vis_container = QVBoxLayout()
        beacon_vis_layout.addLayout(self._beacon_vis_container)
        left_layout.addWidget(beacon_vis_group)

        heatmap_group = QGroupBox("RSS Heatmap")
        heatmap_layout = QVBoxLayout(heatmap_group)

        beacon_sel_layout = QHBoxLayout()
        beacon_sel_layout.addWidget(QLabel("Beacon:"))
        self._combo_heatmap_beacon = QComboBox()
        self._combo_heatmap_beacon.addItem("All (average)")
        beacon_sel_layout.addWidget(self._combo_heatmap_beacon)
        heatmap_layout.addLayout(beacon_sel_layout)

        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("Resolution (m):"))
        self._spin_heatmap_res = QDoubleSpinBox()
        self._spin_heatmap_res.setRange(0.1, 5.0)
        self._spin_heatmap_res.setValue(0.5)
        self._spin_heatmap_res.setSingleStep(0.1)
        self._spin_heatmap_res.setDecimals(1)
        res_layout.addWidget(self._spin_heatmap_res)
        heatmap_layout.addLayout(res_layout)

        self._btn_heatmap = QPushButton("Show Heatmap")
        self._btn_heatmap.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 6px; }"
        )
        heatmap_layout.addWidget(self._btn_heatmap)

        self._btn_hide_heatmap = QPushButton("Hide Heatmap")
        self._btn_hide_heatmap.setEnabled(False)
        heatmap_layout.addWidget(self._btn_hide_heatmap)

        left_layout.addWidget(heatmap_group)

        left_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(left_panel)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setMinimumWidth(340)

        self._right_stack = QStackedWidget()
        self._right_stack.setMinimumWidth(820)

        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)
        plot_layout.setContentsMargins(0, 0, 0, 0)

        self._figure = Figure(figsize=(8, 5))
        self._plot_canvas = FigureCanvas(self._figure)
        self._toolbar = NavigationToolbar(self._plot_canvas, self)

        plot_layout.addWidget(self._toolbar)
        plot_layout.addWidget(self._plot_canvas)

        self._heatmap_canvas = FloorPlanCanvas()

        self._right_stack.addWidget(plot_widget)
        self._right_stack.addWidget(self._heatmap_canvas)
        self._right_stack.setCurrentIndex(0)

        splitter.addWidget(scroll_area)
        splitter.addWidget(self._right_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([360, 900])

        main_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self._btn_generate.clicked.connect(self._on_generate)
        self._btn_heatmap.clicked.connect(self._on_show_heatmap)
        self._btn_hide_heatmap.clicked.connect(self._on_hide_heatmap)
        self._state.building_changed.connect(self._mark_building_ui_dirty)

    def sync_from_state(self) -> None:
        p = self._state.signal_tab_params
        self._spin_rss_sigma.setValue(p.get("rss_sigma", 2.0))
        self._spin_tof_sigma.setValue(p.get("tof_sigma_ns", 1.0))
        self._spin_path_loss.setValue(p.get("path_loss_exponent", 2.0))
        self._spin_wall_att.setValue(p.get("wall_attenuation_db", 3.0))
        self._spin_rssi_at_ref.setValue(p.get("rssi_at_ref", -59.0))
        self._spin_d0.setValue(p.get("d0", 1.0))

    def _mark_building_ui_dirty(self) -> None:
        self._building_ui_dirty = True

    def ensure_building_ui_up_to_date(self) -> None:
        if not self._building_ui_dirty:
            return
        self._update_beacon_checkboxes()
        self._update_heatmap_beacon_combo()
        self._refresh_heatmap_canvas()
        self._building_ui_dirty = False

    def _get_nlos_mode(self) -> NlosMode:
        idx = self._combo_nlos.currentIndex()
        return [NlosMode.NONE, NlosMode.INCREASE_ERROR, NlosMode.SKIP][idx]

    def _get_current_level_walls_doors(self):
        idx = self._state.current_level_index
        levels = self._state.building.levels
        if 0 <= idx < len(levels):
            return levels[idx].walls, levels[idx].doors
        return [], []

    def _on_generate(self) -> None:
        if self._state.ground_truth is None:
            self._lbl_info.setText("Generate a trajectory first!")
            return

        beacons = self._state.building.all_beacons()
        if not beacons:
            self._lbl_info.setText("Place beacons first!")
            return

        type_str = self._combo_type.currentText()
        signal_type = {
            "RSS": SignalType.RSS,
            "ToF": SignalType.TOF,
        }[type_str]

        walls, doors = self._get_current_level_walls_doors()

        signal = generate_beacon_signal(
            ground_truth=self._state.ground_truth,
            beacons=beacons,
            signal_type=signal_type,
            rss_sigma=self._spin_rss_sigma.value(),
            tof_sigma=self._spin_tof_sigma.value() * 1e-9,
            walls=walls,
            doors=doors,
            wall_attenuation_db=self._spin_wall_att.value(),
            nlos_mode=self._get_nlos_mode(),
            nlos_error_multiplier=self._spin_nlos_mult.value(),
            path_loss_exponent=self._spin_path_loss.value(),
            n_samples=self._spin_samples.value(),
            rssi_at_ref=self._spin_rssi_at_ref.value(),
            d0=self._spin_d0.value(),
        )

        self._state.signal_tab_params = {
            "rss_sigma": self._spin_rss_sigma.value(),
            "tof_sigma_ns": self._spin_tof_sigma.value(),
            "path_loss_exponent": self._spin_path_loss.value(),
            "wall_attenuation_db": self._spin_wall_att.value(),
            "rssi_at_ref": self._spin_rssi_at_ref.value(),
            "d0": self._spin_d0.value(),
        }

        self._state.set_beacon_signals([signal])
        self._update_beacon_checkboxes()
        self._plot_signals(signal)
        self._building_ui_dirty = False

        self._right_stack.setCurrentIndex(0)

        self._lbl_info.setText(
            f"Type: {type_str}\n"
            f"Beacons: {signal.n_beacons}\n"
            f"Points: {len(signal.timeline)}\n"
            f"Samples/point: {self._spin_samples.value()}"
        )

    def _on_show_heatmap(self) -> None:
        beacons = self._state.building.all_beacons()
        if not beacons:
            self._lbl_info.setText("Place beacons first!")
            return

        level = None
        idx = self._state.current_level_index
        if 0 <= idx < len(self._state.building.levels):
            level = self._state.building.levels[idx]
        dims = level.dimensions if level else (50.0, 50.0)

        walls, doors = self._get_current_level_walls_doors()

        beacon_combo_idx = self._combo_heatmap_beacon.currentIndex()
        beacon_index = None if beacon_combo_idx == 0 else beacon_combo_idx - 1

        self._btn_heatmap.setEnabled(False)
        self._lbl_info.setText("Computing heatmap...")

        self._heatmap_worker = HeatmapWorker(
            beacons=beacons,
            x_range=(0, dims[0]),
            y_range=(0, dims[1]),
            z=0.0,
            resolution=self._spin_heatmap_res.value(),
            rss_sigma=0.0,
            walls=walls,
            doors=doors,
            wall_attenuation_db=self._spin_wall_att.value(),
            beacon_index=beacon_index,
            path_loss_exponent=self._spin_path_loss.value(),
            rssi_at_ref=self._spin_rssi_at_ref.value(),
            d0=self._spin_d0.value(),
        )
        self._heatmap_worker.finished.connect(self._on_heatmap_ready)
        self._heatmap_worker.error.connect(self._on_heatmap_error)
        self._heatmap_worker.start()

    def _on_heatmap_ready(self, result: object) -> None:
        hm: HeatmapResult = result  # type: ignore[assignment]
        self._btn_heatmap.setEnabled(True)
        self._btn_hide_heatmap.setEnabled(True)

        self._refresh_heatmap_canvas()
        self._heatmap_canvas.set_heatmap_overlay(hm.grid, hm.x_edges, hm.y_edges)
        self._planimetry_canvas.set_heatmap_overlay(hm.grid, hm.x_edges, hm.y_edges)

        self._right_stack.setCurrentIndex(1)

        beacon_label = "All (average)"
        if hm.beacon_index is not None:
            beacons = self._state.building.all_beacons()
            if hm.beacon_index < len(beacons):
                b = beacons[hm.beacon_index]
                beacon_label = b.label or f"B-{hm.beacon_index + 1}"
        self._lbl_info.setText(f"Heatmap: {beacon_label}")

    def _on_heatmap_error(self, msg: str) -> None:
        self._btn_heatmap.setEnabled(True)
        self._lbl_info.setText(f"Heatmap error: {msg}")

    def _on_hide_heatmap(self) -> None:
        self._heatmap_canvas.remove_heatmap_overlay()
        self._planimetry_canvas.remove_heatmap_overlay()
        self._btn_hide_heatmap.setEnabled(False)
        self._right_stack.setCurrentIndex(0)
        self._lbl_info.setText("Heatmap hidden")

    def _refresh_heatmap_canvas(self) -> None:
        self._heatmap_canvas.clear_all()
        self._heatmap_canvas.clear_floor_plan()
        level_idx = self._state.current_level_index
        if 0 <= level_idx < len(self._state.building.levels):
            level = self._state.building.levels[level_idx]
            if level.floor_plan_path:
                self._heatmap_canvas.load_floor_plan(
                    level.floor_plan_path, level.dimensions
                )
            else:
                self._heatmap_canvas.set_dimensions(level.dimensions)
            for w in level.walls:
                self._heatmap_canvas.add_wall(w)
            for d in level.doors:
                self._heatmap_canvas.add_door(d)
            for i, b in enumerate(level.beacons):
                self._heatmap_canvas.add_beacon(b, i)
        self._building_ui_dirty = False

    def _plot_signals(self, signal: BeaconSignal) -> None:
        self._figure.clear()
        ax = self._figure.add_subplot(111)

        colors = [
            "#e74c3c",
            "#3498db",
            "#2ecc71",
            "#f39c12",
            "#9b59b6",
            "#1abc9c",
            "#e67e22",
            "#34495e",
        ]

        for j in range(signal.n_beacons):
            visible = True
            if j < len(self._beacon_checkboxes):
                visible = self._beacon_checkboxes[j].isChecked()
            if not visible:
                continue

            values = signal.values_for_beacon(j)
            color = colors[j % len(colors)]
            label = signal.beacons[j].label or f"B-{j + 1}"
            ax.plot(signal.timeline, values, color=color, label=label, linewidth=0.8)

        unit_map = {
            SignalType.RSS: "RSS (dB)",
            SignalType.TOF: "ToF (s)",
        }
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(unit_map.get(signal.signal_type, "Value"))
        ax.set_title(f"{signal.signal_type.value} Signal")
        ax.legend(loc="best", fontsize=7)
        ax.grid(True, alpha=0.3)

        self._figure.tight_layout()
        self._plot_canvas.draw()

    def _update_beacon_checkboxes(self) -> None:
        for cb in self._beacon_checkboxes:
            self._beacon_vis_container.removeWidget(cb)
            cb.deleteLater()
        self._beacon_checkboxes.clear()

        beacons = self._state.building.all_beacons()
        for i, b in enumerate(beacons):
            cb = QCheckBox(b.label or f"B-{i + 1}")
            cb.setChecked(True)
            cb.toggled.connect(self._on_beacon_visibility_changed)
            self._beacon_vis_container.addWidget(cb)
            self._beacon_checkboxes.append(cb)

    def _update_heatmap_beacon_combo(self) -> None:
        self._combo_heatmap_beacon.clear()
        self._combo_heatmap_beacon.addItem("All (average)")
        beacons = self._state.building.all_beacons()
        for i, b in enumerate(beacons):
            self._combo_heatmap_beacon.addItem(b.label or f"B-{i + 1}")

    def _on_beacon_visibility_changed(self) -> None:
        if self._state.beacon_signals:
            self._plot_signals(self._state.beacon_signals[0])
