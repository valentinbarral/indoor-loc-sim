from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QFileDialog,
    QMessageBox,
    QStatusBar,
)

from indoor_loc_sim.core.models import Building, Level
from indoor_loc_sim.core.project_io import (
    PROJECT_EXTENSION,
    cleanup_temp_dir,
    load_project,
    save_project,
)
from indoor_loc_sim.gui.state import AppState
from indoor_loc_sim.gui.tabs.planimetry_tab import PlanimetryTab
from indoor_loc_sim.gui.tabs.trajectory_tab import TrajectoryTab
from indoor_loc_sim.gui.tabs.signal_tab import SignalGenerationTab
from indoor_loc_sim.gui.tabs.estimation_tab import EstimationTab
from indoor_loc_sim.gui.tabs.analysis_tab import ErrorAnalysisTab
from indoor_loc_sim.gui.widgets.settings_dialog import SettingsDialog

_PROJECT_FILTER = f"Project Files (*{PROJECT_EXTENSION} *.ilsproj)"
_BUILDING_FILTER = "JSON Files (*.json)"
_APP_TITLE = "Indoor Localization Simulator"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_APP_TITLE)
        self.setMinimumSize(1100, 700)

        self._state = AppState()
        self._temp_dir: Path | None = None
        self._current_building_path: Path | None = None
        self._current_project_path: Path | None = None
        self._building_dirty: bool = False
        self._suspend_dirty_tracking: bool = False
        self._setup_tabs()
        self._setup_menu()
        self._setup_status_bar()

    def _setup_tabs(self) -> None:
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._planimetry_tab = PlanimetryTab(self._state)
        planimetry_canvas = self._planimetry_tab.canvas

        self._trajectory_tab = TrajectoryTab(self._state, planimetry_canvas)
        self._signal_tab = SignalGenerationTab(self._state, planimetry_canvas)
        self._estimation_tab = EstimationTab(self._state, planimetry_canvas)
        self._analysis_tab = ErrorAnalysisTab(self._state)

        self._tabs.addTab(self._planimetry_tab, "1. Planimetry")
        self._tabs.addTab(self._trajectory_tab, "2. Trajectories")
        self._tabs.addTab(self._signal_tab, "3. Signals")
        self._tabs.addTab(self._estimation_tab, "4. Estimation")
        self._tabs.addTab(self._analysis_tab, "5. Error Analysis")

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        new_action = QAction("&New Project", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._on_new_project)
        file_menu.addAction(new_action)

        file_menu.addSeparator()

        open_building_action = QAction("Open &Building...", self)
        open_building_action.setShortcut(QKeySequence("Ctrl+O"))
        open_building_action.triggered.connect(self._on_open_building)
        file_menu.addAction(open_building_action)

        save_building_action = QAction("&Save Building", self)
        save_building_action.setShortcut(QKeySequence("Ctrl+S"))
        save_building_action.triggered.connect(self._on_save_building)
        file_menu.addAction(save_building_action)

        save_building_as_action = QAction("Save Building &As...", self)
        save_building_as_action.triggered.connect(self._on_save_building_as)
        file_menu.addAction(save_building_as_action)

        file_menu.addSeparator()

        open_project_action = QAction("Open &Project...", self)
        open_project_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_project_action.triggered.connect(self._on_open_project)
        file_menu.addAction(open_project_action)

        save_project_action = QAction("Save &Project", self)
        save_project_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_project_action.triggered.connect(self._on_save_project)
        file_menu.addAction(save_project_action)

        save_project_as_action = QAction("Save Project As&...", self)
        save_project_as_action.triggered.connect(self._on_save_project_as)
        file_menu.addAction(save_project_as_action)

        file_menu.addSeparator()

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._on_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _setup_status_bar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self._state.building_changed.connect(
            lambda: status.showMessage("Building updated", 3000)
        )
        self._state.building_changed.connect(self._on_building_changed)
        self._state.trajectory_changed.connect(
            lambda: status.showMessage("Trajectory updated", 3000)
        )
        self._state.signals_changed.connect(
            lambda: status.showMessage("Signals generated", 3000)
        )
        self._state.estimation_changed.connect(
            lambda: status.showMessage("Estimation updated", 3000)
        )
        self._state.analysis_changed.connect(
            lambda: status.showMessage("Analysis updated", 3000)
        )

    def _on_tab_changed(self, index: int) -> None:
        planimetry_canvas = self._planimetry_tab.canvas
        if index == 0:
            planimetry_canvas.clear_all_trajectories()
            planimetry_canvas.remove_heatmap_overlay()
        elif index == 1:
            self._trajectory_tab.ensure_canvas_up_to_date()
        elif index == 2:
            self._signal_tab.ensure_building_ui_up_to_date()
        elif index == 3:
            self._estimation_tab.ensure_canvas_up_to_date()

    # ── File operations ──

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._state.signal_tab_params, parent=self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self._state.signal_tab_params.update(dlg.result_params())
            self._signal_tab.sync_from_state()

    def _cleanup_temp(self) -> None:
        cleanup_temp_dir(self._temp_dir)
        self._temp_dir = None

    def _update_window_title(self) -> None:
        current_path = self._current_project_path or self._current_building_path
        if current_path is None:
            base_title = _APP_TITLE
        else:
            base_title = f"{current_path.name} — {_APP_TITLE}"

        if self._building_dirty:
            base_title = f"* {base_title}"

        self.setWindowTitle(base_title)

    def _set_building_dirty(self, dirty: bool) -> None:
        self._building_dirty = dirty
        self._update_window_title()

    def _on_building_changed(self) -> None:
        if not self._suspend_dirty_tracking:
            self._set_building_dirty(True)

    def _set_current_building_path(self, path: str | Path | None) -> None:
        self._current_building_path = Path(path) if path else None
        self._update_window_title()

    def _set_current_project_path(self, path: str | Path | None) -> None:
        self._current_project_path = Path(path) if path else None
        self._update_window_title()

    def _save_building_to_path(self, path: str | Path) -> None:
        self._state.building.save(path)
        self._set_current_building_path(path)
        self._set_building_dirty(False)

    def _save_project_to_path(self, path: str | Path) -> None:
        save_project(
            path,
            self._state.building,
            self._state.waypoints,
            ground_truth=self._state.ground_truth,
            beacon_signals=self._state.beacon_signals,
            simulation_runs=self._state.simulation_runs,
        )
        self._set_current_project_path(path)
        self._set_building_dirty(False)

    def _refresh_ui_after_new_project(self) -> None:
        self._planimetry_tab._refresh_all()

        self._trajectory_tab._refresh_waypoint_list()
        self._trajectory_tab._lbl_info.setText("No trajectory generated")
        self._trajectory_tab._refresh_canvas()
        self._planimetry_tab.canvas.clear_all_trajectories()
        self._planimetry_tab.canvas.remove_heatmap_overlay()

        self._signal_tab._update_beacon_checkboxes()
        self._signal_tab._update_heatmap_beacon_combo()
        self._signal_tab._refresh_heatmap_canvas()
        self._signal_tab._heatmap_canvas.remove_heatmap_overlay()
        self._signal_tab._right_stack.setCurrentIndex(0)
        self._signal_tab._lbl_info.setText("No signals generated")

        self._estimation_tab._canvas.clear_fingerprint_overlay()
        self._estimation_tab._rebuild_history_list()
        self._estimation_tab._rebuild_fp_combo()
        self._estimation_tab._lbl_results.setText("No estimations run")
        self._estimation_tab._refresh_canvas()

        self._analysis_tab._on_analysis_changed()

    def _on_new_project(self) -> None:
        reply = QMessageBox.question(
            self,
            "New Project",
            "Discard current work and start a new project?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._suspend_dirty_tracking = True
            self._cleanup_temp()
            self._state.set_building(Building(levels=[Level(n=0)]))
            self._state.clear_trajectory()
            self._state.set_beacon_signals([])
            self._state.clear_estimations()
            self._set_current_building_path(None)
            self._set_current_project_path(None)
            self._set_building_dirty(False)
            self._suspend_dirty_tracking = False
            self._refresh_ui_after_new_project()

    def _on_open_building(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Building", "", _BUILDING_FILTER
        )
        if path:
            try:
                self._suspend_dirty_tracking = True
                building = Building.load(path)
                self._state.set_building(building)
                self._set_current_building_path(path)
                self._set_current_project_path(None)
                self._set_building_dirty(False)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load building:\n{e}")
            finally:
                self._suspend_dirty_tracking = False

    def _on_save_building(self) -> None:
        if self._current_building_path is not None:
            try:
                self._save_building_to_path(self._current_building_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save building:\n{e}")
            return

        self._on_save_building_as()

    def _on_save_building_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Building", "building.json", _BUILDING_FILTER
        )
        if path:
            try:
                self._save_building_to_path(path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save building:\n{e}")

    def _on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", _PROJECT_FILTER)
        if not path:
            return
        try:
            self._suspend_dirty_tracking = True
            self._cleanup_temp()
            (
                building,
                waypoints,
                temp_dir,
                ground_truth,
                beacon_signals,
                simulation_runs,
            ) = load_project(path)
            self._temp_dir = temp_dir
            self._state.set_building(building)
            self._state.set_waypoints_and_ground_truth(waypoints, ground_truth)
            self._state.set_beacon_signals(beacon_signals)
            self._state.set_simulation_runs(simulation_runs)
            self._set_current_project_path(path)
            self._set_current_building_path(None)
            self._set_building_dirty(False)

            self._trajectory_tab._refresh_waypoint_list()
            self._trajectory_tab._refresh_canvas()
            self._planimetry_tab.canvas.clear_all_trajectories()

            self._signal_tab._update_beacon_checkboxes()
            self._signal_tab._update_heatmap_beacon_combo()
            if beacon_signals:
                self._signal_tab._plot_signals(beacon_signals[0])

            self._estimation_tab._refresh_canvas()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load project:\n{e}")
        finally:
            self._suspend_dirty_tracking = False

    def _on_save_project(self) -> None:
        if self._current_project_path is not None:
            try:
                self._save_project_to_path(self._current_project_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save project:\n{e}")
            return

        self._on_save_project_as()

    def _on_save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            f"project{PROJECT_EXTENSION}",
            _PROJECT_FILTER,
        )
        if not path:
            return
        try:
            self._save_project_to_path(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project:\n{e}")

    def closeEvent(self, event) -> None:
        if self._building_dirty:
            reply = QMessageBox.warning(
                self,
                "Unsaved changes",
                "The building has unsaved changes. Do you want to save, cancel, or exit without saving?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Discard,
                QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                try:
                    if self._current_project_path is not None:
                        self._save_project_to_path(self._current_project_path)
                    elif self._current_building_path is not None:
                        self._save_building_to_path(self._current_building_path)
                    else:
                        self._on_save_building_as()
                except Exception as e:
                    QMessageBox.critical(
                        self, "Error", f"Failed to save before closing:\n{e}"
                    )
                    event.ignore()
                    return

                if self._building_dirty:
                    event.ignore()
                    return
            elif reply != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        self._cleanup_temp()
        super().closeEvent(event)
