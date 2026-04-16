from __future__ import annotations

import threading

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
    QProgressBar,
    QCheckBox,
    QScrollArea,
    QDialog,
)
from PySide6.QtGui import QColor

from indoor_loc_sim.core.models import Beacon, Door, Wall
from indoor_loc_sim.core.trajectory import TrajectoryPoint
from indoor_loc_sim.engine.signals import SignalType
from indoor_loc_sim.engine.tracking import (
    TRACKING_ALGORITHMS,
    estimate_ekf_rss,
    estimate_ekf_rss_accel,
    estimate_ekf_tof,
    estimate_ukf_rss,
    estimate_trilateration_tof,
    estimate_trilateration_rss,
)
from indoor_loc_sim.engine.fingerprint import (
    CancelledError,
    FINGERPRINT_METRICS,
    FingerprintResult,
    build_radio_map,
    estimate_fingerprint_knn,
)
from indoor_loc_sim.engine.analysis import compute_errors
from indoor_loc_sim.gui.state import AppState, SimulationRun
from indoor_loc_sim.gui.widgets.floor_plan_canvas import FloorPlanCanvas


class EstimationWorker(QThread):
    finished = QSignal(str, list)
    fingerprint_finished = QSignal(str, object)
    error = QSignal(str)
    cancelled = QSignal()
    progress = QSignal(str, int, int)

    def __init__(
        self,
        algorithm_name: str,
        signal,
        initial_state: TrajectoryPoint,
        reference_trajectory: list[TrajectoryPoint] | None = None,
        k_nn: int = 3,
        auto_k: bool = True,
        metric: str = "euclidean",
        process_noise: float = 1.0,
        measurement_noise: float = 2.0,
        accel_noise_variance: float = 1e-3,
        rss_min_threshold: float = -90.0,
        path_loss_exponent: float = 2.0,
        walls: list[Wall] | None = None,
        doors: list[Door] | None = None,
        wall_attenuation_db: float = 0.0,
        fp_beacons: list[Beacon] | None = None,
        fp_x_range: tuple[float, float] = (0, 50),
        fp_y_range: tuple[float, float] = (0, 50),
        fp_z: float = 0.0,
        fp_grid_spacing: float = 1.0,
        fp_n_samples: int = 10,
        fp_rss_sigma: float = 2.0,
        fp_wall_attenuation_db: float = 3.0,
        rssi_at_ref: float = -59.0,
        d0: float = 1.0,
        fp_path_loss_exponent: float = 2.0,
    ):
        super().__init__()
        self._algo_name = algorithm_name
        self._signal = signal
        self._initial_state = initial_state
        self._reference_trajectory = reference_trajectory or []
        self._k_nn = k_nn
        self._auto_k = auto_k
        self._metric = metric
        self._process_noise = process_noise
        self._measurement_noise = measurement_noise
        self._accel_noise_variance = accel_noise_variance
        self._rss_min_threshold = rss_min_threshold
        self._path_loss_exponent = path_loss_exponent
        self._walls = walls or []
        self._doors = doors or []
        self._wall_attenuation_db = wall_attenuation_db

        self._fp_beacons = fp_beacons or []
        self._fp_x_range = fp_x_range
        self._fp_y_range = fp_y_range
        self._fp_z = fp_z
        self._fp_grid_spacing = fp_grid_spacing
        self._fp_n_samples = fp_n_samples
        self._fp_rss_sigma = fp_rss_sigma
        self._fp_wall_attenuation_db = fp_wall_attenuation_db
        self._rssi_at_ref = rssi_at_ref
        self._d0 = d0
        self._fp_path_loss_exponent = fp_path_loss_exponent

        self._cancel_flag = threading.Event()

    def cancel(self) -> None:
        self._cancel_flag.set()

    def _is_cancelled(self) -> bool:
        return self._cancel_flag.is_set()

    def _emit_progress(self, phase: str, current: int, total: int) -> None:
        self.progress.emit(phase, current, total)

    def run(self) -> None:
        try:
            if self._algo_name == "pos2D_Fingerprint_RSS":
                self._run_fingerprint()
            elif self._algo_name in TRACKING_ALGORITHMS:
                self._run_tracking()
            else:
                self.error.emit(f"Unknown algorithm: {self._algo_name}")
        except CancelledError:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(f"{self._algo_name}: {e}")

    def _run_fingerprint(self) -> None:
        radio_map = build_radio_map(
            beacons=self._fp_beacons,
            x_range=self._fp_x_range,
            y_range=self._fp_y_range,
            z=self._fp_z,
            grid_spacing=self._fp_grid_spacing,
            n_samples=self._fp_n_samples,
            rss_sigma=self._fp_rss_sigma,
            walls=self._walls,
            doors=self._doors,
            wall_attenuation_db=self._fp_wall_attenuation_db,
            path_loss_exponent=self._fp_path_loss_exponent,
            rssi_at_ref=self._rssi_at_ref,
            d0=self._d0,
            progress_callback=lambda cur, tot: self._emit_progress(
                "Building radio map", cur, tot
            ),
            is_cancelled=self._is_cancelled,
        )

        fp_result = estimate_fingerprint_knn(
            signal=self._signal,
            radio_map=radio_map,
            initial_state=self._initial_state,
            k=self._k_nn,
            auto_k=self._auto_k,
            metric=self._metric,
            progress_callback=lambda cur, tot: self._emit_progress(
                "Estimating positions", cur, tot
            ),
            is_cancelled=self._is_cancelled,
        )
        self.fingerprint_finished.emit(self._algo_name, fp_result)

    def _run_tracking(self) -> None:
        algo = TRACKING_ALGORITHMS[self._algo_name]
        fn = algo["fn"]

        progress_cb = lambda cur, tot: self._emit_progress(
            "Estimating positions", cur, tot
        )

        if self._algo_name in ("pos2D_EKF_RSS", "pos2D_UKF_RSS"):
            result = fn(
                signal=self._signal,
                initial_state=self._initial_state,
                process_noise_std=self._process_noise,
                measurement_noise_std=self._measurement_noise,
                min_rss_threshold=self._rss_min_threshold,
                path_loss_exponent=self._path_loss_exponent,
                walls=self._walls,
                doors=self._doors,
                wall_attenuation_db=self._wall_attenuation_db,
                progress_callback=progress_cb,
                is_cancelled=self._is_cancelled,
                rssi_at_ref=self._rssi_at_ref,
                d0=self._d0,
            )
        elif self._algo_name == "pos2D_EKF_ToF":
            result = fn(
                signal=self._signal,
                initial_state=self._initial_state,
                process_noise_std=self._process_noise,
                measurement_noise_std=self._measurement_noise * 1e-9,
                progress_callback=progress_cb,
                is_cancelled=self._is_cancelled,
            )
        elif self._algo_name == "pos2D_EKF_RSS_Accel":
            result = fn(
                signal=self._signal,
                initial_state=self._initial_state,
                reference_trajectory=self._reference_trajectory,
                process_noise_std=self._process_noise,
                measurement_noise_std=self._measurement_noise,
                path_loss_exponent=self._path_loss_exponent,
                accel_noise_variance=self._accel_noise_variance,
                min_rss_threshold=self._rss_min_threshold,
                walls=self._walls,
                doors=self._doors,
                wall_attenuation_db=self._wall_attenuation_db,
                progress_callback=progress_cb,
                is_cancelled=self._is_cancelled,
                rssi_at_ref=self._rssi_at_ref,
                d0=self._d0,
            )
        elif self._algo_name == "pos2D_Tri_RSS":
            result = fn(
                signal=self._signal,
                initial_state=self._initial_state,
                path_loss_exponent=self._path_loss_exponent,
                min_rss_threshold=self._rss_min_threshold,
                walls=self._walls,
                doors=self._doors,
                wall_attenuation_db=self._wall_attenuation_db,
                progress_callback=progress_cb,
                is_cancelled=self._is_cancelled,
                rssi_at_ref=self._rssi_at_ref,
                d0=self._d0,
            )
        else:
            result = fn(
                signal=self._signal,
                initial_state=self._initial_state,
                progress_callback=progress_cb,
                is_cancelled=self._is_cancelled,
            )
        self.finished.emit(self._algo_name, result)


class SimulationProgressDialog(QDialog):
    def __init__(self, worker: EstimationWorker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self.setWindowTitle("Simulation in progress")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        self._lbl_phase = QLabel("Starting...")
        self._lbl_phase.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._lbl_phase)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        layout.addWidget(self._progress_bar)

        self._lbl_detail = QLabel("")
        self._lbl_detail.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(self._lbl_detail)

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; "
            "font-weight: bold; padding: 6px 20px; }"
            "QPushButton:hover { background-color: #e74c3c; }"
        )
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._btn_cancel.clicked.connect(self._on_cancel)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_done)
        worker.fingerprint_finished.connect(self._on_done)
        worker.error.connect(self._on_done)
        worker.cancelled.connect(self._on_done)

    def _on_progress(self, phase: str, current: int, total: int) -> None:
        self._lbl_phase.setText(phase)
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            pct = int(100 * current / total)
            self._lbl_detail.setText(f"{current} / {total}  ({pct}%)")
        else:
            self._progress_bar.setRange(0, 0)
            self._lbl_detail.setText("")

    def _on_cancel(self) -> None:
        self._btn_cancel.setEnabled(False)
        self._lbl_phase.setText("Cancelling...")
        self._worker.cancel()

    def _on_done(self, *_args) -> None:
        self.accept()


class _RunEntryWidget(QWidget):
    visibility_changed = QSignal(str, bool)
    delete_requested = QSignal(str)

    def __init__(self, run: SimulationRun, parent=None):
        super().__init__(parent)
        self._run_id = run.run_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)

        self._cb = QCheckBox()
        self._cb.setChecked(run.visible)
        self._cb.toggled.connect(
            lambda checked: self.visibility_changed.emit(self._run_id, checked)
        )
        layout.addWidget(self._cb)

        color_dot = QLabel("●")
        color_dot.setStyleSheet(f"color: {run.color.name()}; font-size: 14px;")
        color_dot.setFixedWidth(16)
        layout.addWidget(color_dot)

        params_parts = []
        for k, v in run.params.items():
            if isinstance(v, float):
                params_parts.append(f"{k}={v:.2g}")
            else:
                params_parts.append(f"{k}={v}")
        params_str = ", ".join(params_parts)
        label_text = f"<b>{run.display_label}</b><br><span style='font-size:8pt;color:#888;'>{params_str}</span>"

        lbl = QLabel(label_text)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(lbl, stretch=1)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet(
            "QPushButton { border: none; color: #c0392b; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { color: #e74c3c; background: #f8d7da; border-radius: 3px; }"
        )
        btn_del.clicked.connect(lambda: self.delete_requested.emit(self._run_id))
        layout.addWidget(btn_del)


class EstimationTab(QWidget):
    def __init__(
        self, state: AppState, planimetry_canvas: FloorPlanCanvas, parent=None
    ):
        super().__init__(parent)
        self._state = state
        self._planimetry_canvas = planimetry_canvas
        self._workers: list[EstimationWorker] = []
        self._run_widgets: dict[str, _RunEntryWidget] = {}
        self._canvas_dirty = True
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        algo_group = QGroupBox("Algorithm Selection")
        algo_layout = QVBoxLayout(algo_group)

        self._combo_algo = QComboBox()
        self._combo_algo.addItems(
            [
                "pos2D_EKF_RSS",
                "pos2D_EKF_ToF",
                "pos2D_EKF_RSS_Accel",
                "pos2D_UKF_RSS",
                "pos2D_Tri_ToF",
                "pos2D_Tri_RSS",
                "pos2D_Fingerprint_RSS",
            ]
        )
        algo_layout.addWidget(self._combo_algo)

        self._lbl_algo_info = QLabel(
            "EKF + RSS: Extended Kalman Filter with RSS signals"
        )
        self._lbl_algo_info.setWordWrap(True)
        algo_layout.addWidget(self._lbl_algo_info)

        left_layout.addWidget(algo_group)

        self._params_group = QGroupBox("Algorithm Parameters")
        params_layout = QVBoxLayout(self._params_group)

        pn_layout = QHBoxLayout()
        self._lbl_process_noise = QLabel("Process noise σ:")
        pn_layout.addWidget(self._lbl_process_noise)
        self._spin_process_noise = QDoubleSpinBox()
        self._spin_process_noise.setRange(0.001, 100.0)
        self._spin_process_noise.setValue(1.0)
        self._spin_process_noise.setSingleStep(0.1)
        pn_layout.addWidget(self._spin_process_noise)
        params_layout.addLayout(pn_layout)

        mn_layout = QHBoxLayout()
        self._lbl_meas_noise = QLabel("Measurement noise σ (dB):")
        mn_layout.addWidget(self._lbl_meas_noise)
        self._spin_meas_noise = QDoubleSpinBox()
        self._spin_meas_noise.setRange(0.5, 100.0)
        self._spin_meas_noise.setValue(2.0)
        self._spin_meas_noise.setSingleStep(0.5)
        mn_layout.addWidget(self._spin_meas_noise)
        params_layout.addLayout(mn_layout)

        an_layout = QHBoxLayout()
        self._lbl_accel_noise = QLabel("Accelerometer noise variance:")
        an_layout.addWidget(self._lbl_accel_noise)
        self._spin_accel_noise_var = QDoubleSpinBox()
        self._spin_accel_noise_var.setRange(0.000001, 100.0)
        self._spin_accel_noise_var.setValue(0.001)
        self._spin_accel_noise_var.setSingleStep(0.001)
        self._spin_accel_noise_var.setDecimals(6)
        an_layout.addWidget(self._spin_accel_noise_var)
        params_layout.addLayout(an_layout)

        rss_thresh_layout = QHBoxLayout()
        self._lbl_rss_threshold = QLabel("RSS min threshold (dBm):")
        rss_thresh_layout.addWidget(self._lbl_rss_threshold)
        self._spin_rss_threshold = QDoubleSpinBox()
        self._spin_rss_threshold.setRange(-200.0, 0.0)
        self._spin_rss_threshold.setValue(-90.0)
        self._spin_rss_threshold.setSingleStep(1.0)
        self._spin_rss_threshold.setDecimals(1)
        rss_thresh_layout.addWidget(self._spin_rss_threshold)
        params_layout.addLayout(rss_thresh_layout)

        left_layout.addWidget(self._params_group)

        self._fp_group = QGroupBox("Fingerprint Parameters")
        fp_layout = QVBoxLayout(self._fp_group)

        grid_layout = QHBoxLayout()
        grid_layout.addWidget(QLabel("Grid spacing (m):"))
        self._spin_grid = QDoubleSpinBox()
        self._spin_grid.setRange(0.1, 10.0)
        self._spin_grid.setValue(1.0)
        self._spin_grid.setSingleStep(0.5)
        grid_layout.addWidget(self._spin_grid)
        fp_layout.addLayout(grid_layout)

        knn_layout = QHBoxLayout()
        knn_layout.addWidget(QLabel("k (neighbors):"))
        self._spin_knn = QSpinBox()
        self._spin_knn.setRange(1, 200)
        self._spin_knn.setValue(3)
        knn_layout.addWidget(self._spin_knn)
        fp_layout.addLayout(knn_layout)

        autok_layout = QHBoxLayout()
        self._cb_auto_k = QCheckBox("Auto-scale k with grid density")
        self._cb_auto_k.setChecked(True)
        self._cb_auto_k.setToolTip(
            "Automatically increase k for finer grids to keep the spatial "
            "averaging area constant (recommended)"
        )
        autok_layout.addWidget(self._cb_auto_k)
        fp_layout.addLayout(autok_layout)

        samples_layout = QHBoxLayout()
        samples_layout.addWidget(QLabel("Samples per point:"))
        self._spin_samples = QSpinBox()
        self._spin_samples.setRange(1, 100)
        self._spin_samples.setValue(10)
        samples_layout.addWidget(self._spin_samples)
        fp_layout.addLayout(samples_layout)

        metric_layout = QHBoxLayout()
        metric_layout.addWidget(QLabel("Distance metric:"))
        self._combo_metric = QComboBox()
        self._combo_metric.addItems(list(FINGERPRINT_METRICS.keys()))
        metric_layout.addWidget(self._combo_metric)
        fp_layout.addLayout(metric_layout)

        left_layout.addWidget(self._fp_group)

        btn_layout = QVBoxLayout()
        self._btn_estimate = QPushButton("Run Estimation")
        self._btn_estimate.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 8px; }"
        )
        btn_layout.addWidget(self._btn_estimate)

        self._btn_clear = QPushButton("Clear All")
        btn_layout.addWidget(self._btn_clear)

        left_layout.addLayout(btn_layout)

        results_group = QGroupBox("Last Result")
        results_layout = QVBoxLayout(results_group)
        self._lbl_results = QLabel("No estimations run")
        self._lbl_results.setWordWrap(True)
        results_layout.addWidget(self._lbl_results)
        left_layout.addWidget(results_group)

        history_group = QGroupBox("Simulation History")
        history_layout = QVBoxLayout(history_group)
        self._history_container = QVBoxLayout()
        history_layout.addLayout(self._history_container)
        self._lbl_no_history = QLabel("No simulations yet")
        self._lbl_no_history.setStyleSheet("color: #888;")
        history_layout.addWidget(self._lbl_no_history)
        left_layout.addWidget(history_group)

        fp_overlay_group = QGroupBox("Fingerprint Overlay")
        fp_overlay_layout = QVBoxLayout(fp_overlay_group)
        self._combo_fp_run = QComboBox()
        self._combo_fp_run.setEnabled(False)
        fp_overlay_layout.addWidget(self._combo_fp_run)
        self._btn_show_fp = QPushButton("Show Fingerprints")
        self._btn_show_fp.setCheckable(True)
        self._btn_show_fp.setEnabled(False)
        self._btn_show_fp.setStyleSheet(
            "QPushButton:checked { background-color: #16a085; color: white; font-weight: bold; }"
        )
        fp_overlay_layout.addWidget(self._btn_show_fp)
        left_layout.addWidget(fp_overlay_group)

        left_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(left_panel)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setMinimumWidth(340)

        self._canvas = FloorPlanCanvas()
        self._canvas.setMinimumWidth(820)
        splitter.addWidget(scroll_area)
        splitter.addWidget(self._canvas)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([360, 900])

        main_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self._combo_algo.currentTextChanged.connect(self._on_algo_changed)
        self._btn_estimate.clicked.connect(self._on_estimate)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_show_fp.toggled.connect(self._on_toggle_fingerprint_overlay)
        self._combo_fp_run.currentIndexChanged.connect(
            self._on_fp_run_selection_changed
        )
        self._state.building_changed.connect(self._mark_canvas_dirty)
        self._state.trajectory_changed.connect(self._refresh_canvas)
        self._state.signals_changed.connect(self._sync_params_from_signal_tab)
        self._state.estimation_changed.connect(self._rebuild_history_list)
        self._state.estimation_changed.connect(self._rebuild_fp_combo)
        self._on_algo_changed(self._combo_algo.currentText())

    def _mark_canvas_dirty(self) -> None:
        self._canvas_dirty = True

    def ensure_canvas_up_to_date(self) -> None:
        if self._canvas_dirty:
            self._refresh_canvas()

    def _on_algo_changed(self, text: str) -> None:
        info_map = {
            "pos2D_EKF_RSS": "Extended Kalman Filter with RSS signals",
            "pos2D_EKF_ToF": "Extended Kalman Filter with ToF signals",
            "pos2D_EKF_RSS_Accel": "Extended Kalman Filter with RSS and a simulated accelerometer",
            "pos2D_UKF_RSS": "Unscented Kalman Filter with RSS signals",
            "pos2D_Tri_ToF": "Trilateration (multilateration) with ToF signals",
            "pos2D_Tri_RSS": "RSS-based trilateration using distance estimates",
            "pos2D_Fingerprint_RSS": "Fingerprint matching with k-NN using RSS radio map",
        }
        self._lbl_algo_info.setText(info_map.get(text, ""))

        if text in ("pos2D_EKF_ToF", "pos2D_Tri_ToF"):
            self._lbl_meas_noise.setText("Measurement noise σ (ns):")
            self._spin_meas_noise.setRange(0.0, 1000.0)
            self._spin_meas_noise.setSingleStep(0.1)
            self._spin_meas_noise.setDecimals(1)
        else:
            self._lbl_meas_noise.setText("Measurement noise σ (dB):")
            self._spin_meas_noise.setRange(0.5, 100.0)
            self._spin_meas_noise.setSingleStep(0.5)
            self._spin_meas_noise.setDecimals(1)

        accel_enabled = text == "pos2D_EKF_RSS_Accel"
        self._lbl_accel_noise.setEnabled(accel_enabled)
        self._spin_accel_noise_var.setEnabled(accel_enabled)

        rss_threshold_enabled = text in (
            "pos2D_EKF_RSS",
            "pos2D_EKF_RSS_Accel",
            "pos2D_UKF_RSS",
            "pos2D_Tri_RSS",
        )
        self._lbl_rss_threshold.setEnabled(rss_threshold_enabled)
        self._spin_rss_threshold.setEnabled(rss_threshold_enabled)

        process_enabled = text in (
            "pos2D_EKF_RSS",
            "pos2D_EKF_ToF",
            "pos2D_EKF_RSS_Accel",
            "pos2D_UKF_RSS",
        )
        meas_enabled = text in (
            "pos2D_EKF_RSS",
            "pos2D_EKF_ToF",
            "pos2D_EKF_RSS_Accel",
            "pos2D_UKF_RSS",
        )
        fingerprint_enabled = text == "pos2D_Fingerprint_RSS"

        self._lbl_process_noise.setEnabled(process_enabled)
        self._spin_process_noise.setEnabled(process_enabled)
        self._lbl_meas_noise.setEnabled(meas_enabled)
        self._spin_meas_noise.setEnabled(meas_enabled)

        self._spin_grid.setEnabled(fingerprint_enabled)
        self._spin_knn.setEnabled(fingerprint_enabled)
        self._cb_auto_k.setEnabled(fingerprint_enabled)
        self._spin_samples.setEnabled(fingerprint_enabled)
        self._combo_metric.setEnabled(fingerprint_enabled)
        self._fp_group.setEnabled(fingerprint_enabled)

    def _sync_params_from_signal_tab(self) -> None:
        params = self._state.signal_tab_params
        algo_name = self._combo_algo.currentText()
        if algo_name in ("pos2D_EKF_ToF", "pos2D_Tri_ToF"):
            self._spin_meas_noise.setValue(params.get("tof_sigma_ns", 1.0))
        else:
            self._spin_meas_noise.setValue(params.get("rss_sigma", 2.0))

    def _collect_run_params(self, algo_name: str) -> dict[str, float | int | str]:
        sig_p = self._state.signal_tab_params
        path_loss_exponent = sig_p.get("path_loss_exponent", 2.0)
        params: dict[str, float | int | str] = {}

        if algo_name in ("pos2D_EKF_RSS", "pos2D_UKF_RSS"):
            params["σ_p"] = self._spin_process_noise.value()
            params["σ_m"] = self._spin_meas_noise.value()
            params["rss_min"] = self._spin_rss_threshold.value()
            params["n"] = path_loss_exponent
            params["A"] = sig_p.get("rssi_at_ref", -59.0)
            params["d₀"] = sig_p.get("d0", 1.0)
            params["wall_att"] = sig_p.get("wall_attenuation_db", 0.0)
        elif algo_name == "pos2D_EKF_ToF":
            params["σ_p"] = self._spin_process_noise.value()
            params["σ_tof_ns"] = self._spin_meas_noise.value()
            params["type"] = "ToF"
        elif algo_name == "pos2D_EKF_RSS_Accel":
            params["σ_p"] = self._spin_process_noise.value()
            params["σ_m"] = self._spin_meas_noise.value()
            params["σ²_acc"] = self._spin_accel_noise_var.value()
            params["rss_min"] = self._spin_rss_threshold.value()
            params["n"] = path_loss_exponent
            params["A"] = sig_p.get("rssi_at_ref", -59.0)
            params["d₀"] = sig_p.get("d0", 1.0)
            params["wall_att"] = sig_p.get("wall_attenuation_db", 0.0)
        elif algo_name == "pos2D_Tri_ToF":
            params["type"] = "ToF"
        elif algo_name == "pos2D_Tri_RSS":
            params["rss_min"] = self._spin_rss_threshold.value()
            params["n"] = path_loss_exponent
            params["A"] = sig_p.get("rssi_at_ref", -59.0)
            params["d₀"] = sig_p.get("d0", 1.0)
            params["wall_att"] = sig_p.get("wall_attenuation_db", 0.0)
        elif algo_name == "pos2D_Fingerprint_RSS":
            params["grid"] = self._spin_grid.value()
            params["k"] = self._spin_knn.value()
            params["auto_k"] = self._cb_auto_k.isChecked()
            params["samples"] = self._spin_samples.value()
            params["metric"] = self._combo_metric.currentText()
            params["σ_rss"] = sig_p.get("rss_sigma", 2.0)
            params["A"] = sig_p.get("rssi_at_ref", -59.0)
            params["d₀"] = sig_p.get("d0", 1.0)

        return params

    def _on_estimate(self) -> None:
        if not self._state.beacon_signals:
            self._lbl_results.setText("Generate signals first!")
            return
        if self._state.ground_truth is None:
            self._lbl_results.setText("Generate a trajectory first!")
            return

        algo_name = self._combo_algo.currentText()
        signal = self._state.beacon_signals[0]
        initial_state = self._state.ground_truth.events[0]

        required_signal_type = None
        if algo_name in TRACKING_ALGORITHMS:
            required_signal_type = TRACKING_ALGORITHMS[algo_name]["signal_type"]
        elif algo_name == "pos2D_Fingerprint_RSS":
            required_signal_type = SignalType.RSS

        if required_signal_type and signal.signal_type != required_signal_type:
            self._lbl_results.setText(
                f"Algorithm requires {required_signal_type.value} signal, "
                f"but current signal is {signal.signal_type.value}!"
            )
            return

        sig_tab = self._state.signal_tab_params
        path_loss_exponent = sig_tab.get("path_loss_exponent", 2.0)
        level = None
        level_idx = self._state.current_level_index
        if 0 <= level_idx < len(self._state.building.levels):
            level = self._state.building.levels[level_idx]

        fp_kwargs: dict = {}
        if algo_name == "pos2D_Fingerprint_RSS":
            beacons = self._state.building.all_beacons()
            dims = level.dimensions if level else (50.0, 50.0)
            fp_kwargs = dict(
                fp_beacons=beacons,
                fp_x_range=(0, dims[0]),
                fp_y_range=(0, dims[1]),
                fp_z=initial_state.z,
                fp_grid_spacing=self._spin_grid.value(),
                fp_n_samples=self._spin_samples.value(),
                fp_rss_sigma=sig_tab.get("rss_sigma", 2.0),
                fp_wall_attenuation_db=sig_tab.get("wall_attenuation_db", 3.0),
                fp_path_loss_exponent=sig_tab.get("path_loss_exponent", 2.0),
            )

        worker = EstimationWorker(
            algorithm_name=algo_name,
            signal=signal,
            initial_state=initial_state,
            reference_trajectory=self._state.ground_truth.events,
            k_nn=self._spin_knn.value(),
            auto_k=self._cb_auto_k.isChecked(),
            metric=FINGERPRINT_METRICS.get(
                self._combo_metric.currentText(), "euclidean"
            ),
            process_noise=self._spin_process_noise.value(),
            measurement_noise=self._spin_meas_noise.value(),
            accel_noise_variance=self._spin_accel_noise_var.value(),
            rss_min_threshold=self._spin_rss_threshold.value(),
            path_loss_exponent=path_loss_exponent,
            walls=level.walls if level else [],
            doors=level.doors if level else [],
            wall_attenuation_db=sig_tab.get("wall_attenuation_db", 0.0),
            rssi_at_ref=sig_tab.get("rssi_at_ref", -59.0),
            d0=sig_tab.get("d0", 1.0),
            **fp_kwargs,
        )
        worker.finished.connect(self._on_estimation_done)
        worker.fingerprint_finished.connect(self._on_fingerprint_done)
        worker.error.connect(self._on_estimation_error)
        worker.cancelled.connect(self._on_estimation_cancelled)
        self._workers.append(worker)

        self._btn_estimate.setEnabled(False)

        dialog = SimulationProgressDialog(worker, parent=self)
        worker.start()
        dialog.exec()

        self._btn_estimate.setEnabled(True)

    def _create_and_store_run(
        self,
        algo_name: str,
        trajectory: list[TrajectoryPoint],
        fp_result: FingerprintResult | None = None,
    ) -> SimulationRun:
        gt_events = self._state.ground_truth.events if self._state.ground_truth else []
        run_id, display_label, color = self._state.next_run_id(algo_name)
        analysis = compute_errors(gt_events, trajectory, label=display_label)
        params = self._collect_run_params(algo_name)

        run = SimulationRun(
            run_id=run_id,
            algorithm=algo_name,
            display_label=display_label,
            params=params,
            trajectory=trajectory,
            analysis=analysis,
            color=color,
            fingerprint_result=fp_result,
        )
        self._state.add_simulation_run(run)
        self._canvas.draw_estimated_trajectory(run.run_id, trajectory, color)
        self._planimetry_canvas.draw_estimated_trajectory(run.run_id, trajectory, color)
        return run

    def _on_estimation_done(self, name: str, trajectory: list) -> None:
        run = self._create_and_store_run(name, trajectory)
        self._show_run_results(run)

    def _on_estimation_error(self, msg: str) -> None:
        self._lbl_results.setText(f"Error: {msg}")

    def _on_estimation_cancelled(self) -> None:
        self._lbl_results.setText("Simulation cancelled")

    def _on_fingerprint_done(self, name: str, fp_result: object) -> None:
        result: FingerprintResult = fp_result  # type: ignore[assignment]
        run = self._create_and_store_run(name, result.trajectory, fp_result=result)
        self._show_run_results(run)

    def _show_run_results(self, run: SimulationRun) -> None:
        a = run.analysis
        self._lbl_results.setText(
            f"{run.display_label}:\n"
            f"  Mean error: {a.mean_error:.3f} m\n"
            f"  90th percentile: {a.percentile_90:.3f} m\n"
            f"  Max error: {a.max_error:.3f} m"
        )

    # ── History list ──

    def _rebuild_history_list(self) -> None:
        for w in self._run_widgets.values():
            self._history_container.removeWidget(w)
            w.deleteLater()
        self._run_widgets.clear()

        runs = self._state.simulation_runs
        self._lbl_no_history.setVisible(len(runs) == 0)

        for run in runs:
            widget = _RunEntryWidget(run)
            widget.visibility_changed.connect(self._on_run_visibility_changed)
            widget.delete_requested.connect(self._on_run_delete_requested)
            self._history_container.addWidget(widget)
            self._run_widgets[run.run_id] = widget

    def _on_run_visibility_changed(self, run_id: str, visible: bool) -> None:
        for run in self._state.simulation_runs:
            if run.run_id == run_id:
                run.visible = visible
                break
        self._redraw_trajectories()
        self._state.analysis_changed.emit()

    def _on_run_delete_requested(self, run_id: str) -> None:
        if self._btn_show_fp.isChecked():
            idx = self._combo_fp_run.currentIndex()
            if idx >= 0:
                fp_runs = self._state.fingerprint_runs()
                if idx < len(fp_runs) and fp_runs[idx].run_id == run_id:
                    self._btn_show_fp.setChecked(False)

        self._canvas._clear_trajectory_path(run_id)
        self._planimetry_canvas._clear_trajectory_path(run_id)
        self._state.remove_simulation_run(run_id)

    def _redraw_trajectories(self) -> None:
        self._canvas.clear_all_trajectories()
        self._planimetry_canvas.clear_all_trajectories()

        if self._state.ground_truth:
            self._canvas.draw_real_trajectory(self._state.ground_truth.events)

        for run in self._state.simulation_runs:
            if run.visible:
                self._canvas.draw_estimated_trajectory(
                    run.run_id, run.trajectory, run.color
                )
                self._planimetry_canvas.draw_estimated_trajectory(
                    run.run_id, run.trajectory, run.color
                )

    # ── Fingerprint overlay ──

    def _rebuild_fp_combo(self) -> None:
        self._combo_fp_run.blockSignals(True)
        prev_run_id = None
        if self._combo_fp_run.currentIndex() >= 0:
            prev_run_id = self._combo_fp_run.currentData()

        self._combo_fp_run.clear()
        fp_runs = self._state.fingerprint_runs()
        has_fp = len(fp_runs) > 0
        self._combo_fp_run.setEnabled(has_fp)
        self._btn_show_fp.setEnabled(has_fp)

        if not has_fp:
            self._btn_show_fp.setChecked(False)
            self._combo_fp_run.blockSignals(False)
            return

        restore_idx = 0
        for i, run in enumerate(fp_runs):
            self._combo_fp_run.addItem(run.display_label, run.run_id)
            if run.run_id == prev_run_id:
                restore_idx = i

        self._combo_fp_run.setCurrentIndex(restore_idx)
        self._combo_fp_run.blockSignals(False)

    def _on_fp_run_selection_changed(self, _index: int) -> None:
        if self._btn_show_fp.isChecked():
            self._show_selected_fp_overlay()

    def _on_toggle_fingerprint_overlay(self, checked: bool) -> None:
        if not checked:
            self._canvas.clear_fingerprint_overlay()
            return
        self._show_selected_fp_overlay()

    def _show_selected_fp_overlay(self) -> None:
        self._canvas.clear_fingerprint_overlay()
        idx = self._combo_fp_run.currentIndex()
        if idx < 0:
            return

        fp_runs = self._state.fingerprint_runs()
        if idx >= len(fp_runs):
            return

        run = fp_runs[idx]
        fp = run.fingerprint_result
        if fp is None or fp.radio_map is None:
            return

        gt_events = self._state.ground_truth.events if self._state.ground_truth else []
        self._canvas.show_fingerprint_overlay(
            radio_map_positions=fp.radio_map.positions,
            trajectory_points=gt_events[: len(fp.trajectory)],
            estimated_points=fp.trajectory,
            neighbor_indices=fp.neighbor_indices,
        )

    # ── Clear / canvas ──

    def _on_clear(self) -> None:
        self._btn_show_fp.setChecked(False)
        self._canvas.clear_all_trajectories()
        self._canvas.clear_fingerprint_overlay()
        self._planimetry_canvas.clear_all_trajectories()
        self._state.clear_estimations()
        self._lbl_results.setText("No estimations run")
        self._refresh_canvas()

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
            for w in level.walls:
                self._canvas.add_wall(w)
            for d in level.doors:
                self._canvas.add_door(d)
            for i, b in enumerate(level.beacons):
                self._canvas.add_beacon(b, i)

        if self._state.ground_truth:
            self._canvas.draw_real_trajectory(self._state.ground_truth.events)

        for run in self._state.simulation_runs:
            if run.visible:
                self._canvas.draw_estimated_trajectory(
                    run.run_id, run.trajectory, run.color
                )
