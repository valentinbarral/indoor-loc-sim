from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QSplitter,
    QCheckBox,
    QComboBox,
    QScrollArea,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from indoor_loc_sim.gui.state import AppState, SimulationRun


class ErrorAnalysisTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state = state
        self._checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)

        plot_group = QGroupBox("Plot Type")
        plot_layout = QVBoxLayout(plot_group)
        self._combo_plot = QComboBox()
        self._combo_plot.addItems(
            [
                "CDF of Errors",
                "Error over Time",
                "X Error over Time",
                "Y Error over Time",
            ]
        )
        plot_layout.addWidget(self._combo_plot)
        left_layout.addWidget(plot_group)

        vis_group = QGroupBox("Simulations")
        vis_layout = QVBoxLayout(vis_group)
        self._checkbox_container = QVBoxLayout()
        vis_layout.addLayout(self._checkbox_container)
        self._lbl_no_data = QLabel("Run estimations first")
        vis_layout.addWidget(self._lbl_no_data)
        left_layout.addWidget(vis_group)

        btn_layout = QVBoxLayout()
        self._btn_refresh = QPushButton("Refresh Plot")
        self._btn_refresh.setStyleSheet(
            "QPushButton { background-color: #16a085; color: white; "
            "font-weight: bold; padding: 8px; }"
        )
        btn_layout.addWidget(self._btn_refresh)

        self._btn_export = QPushButton("Export Data (CSV)")
        btn_layout.addWidget(self._btn_export)

        left_layout.addLayout(btn_layout)

        summary_group = QGroupBox("Summary")
        summary_layout = QVBoxLayout(summary_group)
        self._lbl_summary = QLabel("No analysis data")
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setTextFormat(Qt.TextFormat.RichText)
        summary_layout.addWidget(self._lbl_summary)
        left_layout.addWidget(summary_group)

        left_layout.addStretch()

        plot_widget = QWidget()
        plot_vlayout = QVBoxLayout(plot_widget)
        plot_vlayout.setContentsMargins(0, 0, 0, 0)

        self._figure = Figure(figsize=(8, 5))
        self._plot_canvas = FigureCanvas(self._figure)
        self._toolbar = NavigationToolbar(self._plot_canvas, self)

        plot_vlayout.addWidget(self._toolbar)
        plot_vlayout.addWidget(self._plot_canvas)

        splitter.addWidget(left_panel)
        splitter.addWidget(plot_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self._btn_refresh.clicked.connect(self._refresh_plot)
        self._btn_export.clicked.connect(self._export_csv)
        self._combo_plot.currentIndexChanged.connect(self._refresh_plot)
        self._state.analysis_changed.connect(self._on_analysis_changed)

    # ── Data sync ──

    def _on_analysis_changed(self) -> None:
        self._rebuild_checkboxes()
        self._update_summary()
        self._refresh_plot()

    def _rebuild_checkboxes(self) -> None:
        for cb in self._checkboxes.values():
            self._checkbox_container.removeWidget(cb)
            cb.deleteLater()
        self._checkboxes.clear()

        runs = self._state.simulation_runs
        self._lbl_no_data.setVisible(len(runs) == 0)

        for run in runs:
            cb = QCheckBox(run.display_label)
            cb.setChecked(run.visible)
            cb.setStyleSheet(f"QCheckBox {{ color: {run.color.name()}; }}")
            cb.toggled.connect(
                lambda checked, rid=run.run_id: self._on_checkbox_toggled(rid, checked)
            )
            self._checkbox_container.addWidget(cb)
            self._checkboxes[run.run_id] = cb

    def _on_checkbox_toggled(self, run_id: str, checked: bool) -> None:
        for run in self._state.simulation_runs:
            if run.run_id == run_id:
                run.visible = checked
                break
        self._state.estimation_changed.emit()
        self._refresh_plot()
        self._update_summary()

    def _selected_runs(self) -> list[SimulationRun]:
        return [
            run
            for run in self._state.simulation_runs
            if run.run_id in self._checkboxes
            and self._checkboxes[run.run_id].isChecked()
        ]

    # ── Summary ──

    def _update_summary(self) -> None:
        runs = self._selected_runs()
        if not runs:
            self._lbl_summary.setText("No analysis data")
            return

        rows = []
        rows.append(
            "<table style='font-size:9pt;'>"
            "<tr><th>Run</th><th>Mean</th><th>P50</th><th>P90</th><th>Max</th></tr>"
        )
        for run in runs:
            a = run.analysis
            rows.append(
                f"<tr><td>{run.display_label}</td>"
                f"<td>{a.mean_error:.3f}</td>"
                f"<td>{a.percentile_50:.3f}</td>"
                f"<td>{a.percentile_90:.3f}</td>"
                f"<td>{a.max_error:.3f}</td></tr>"
            )
        rows.append("</table><br><i>All values in meters</i>")
        self._lbl_summary.setText("".join(rows))

    # ── Plotting ──

    def _refresh_plot(self) -> None:
        plot_type = self._combo_plot.currentIndex()
        visible_runs = self._selected_runs()

        self._figure.clear()
        ax = self._figure.add_subplot(111)

        if not visible_runs:
            ax.set_title("No data to display")
            ax.text(
                0.5,
                0.5,
                "Run estimations and select\nsimulations to compare",
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=12,
                color="gray",
            )
            self._figure.tight_layout()
            self._plot_canvas.draw()
            return

        if plot_type == 0:
            self._plot_cdf(ax, visible_runs)
        elif plot_type == 1:
            self._plot_error_time(ax, visible_runs)
        elif plot_type == 2:
            self._plot_error_x_time(ax, visible_runs)
        elif plot_type == 3:
            self._plot_error_y_time(ax, visible_runs)

        self._figure.tight_layout()
        self._plot_canvas.draw()

    def _plot_cdf(self, ax, runs: list[SimulationRun]) -> None:
        for run in runs:
            sorted_errors, cdf_values = run.analysis.cdf()
            if len(sorted_errors) == 0:
                continue
            ax.plot(
                sorted_errors,
                cdf_values,
                color=run.color.name(),
                label=run.display_label,
                linewidth=1.5,
            )

        ax.axhline(y=0.5, color="gray", linestyle=":", alpha=0.4)
        ax.axhline(y=0.9, color="gray", linestyle=":", alpha=0.4)
        ax.set_xlabel("Error (m)")
        ax.set_ylabel("CDF")
        ax.set_title("Cumulative Distribution Function of Position Errors")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(left=0)
        ax.set_ylim(0, 1.05)

    def _plot_error_time(self, ax, runs: list[SimulationRun]) -> None:
        for run in runs:
            a = run.analysis
            if len(a.errors) == 0:
                continue
            ax.plot(
                a.times,
                a.errors,
                color=run.color.name(),
                label=run.display_label,
                linewidth=0.8,
            )
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Position Error (m)")
        ax.set_title("Position Error over Time")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)

    def _plot_error_x_time(self, ax, runs: list[SimulationRun]) -> None:
        for run in runs:
            a = run.analysis
            if len(a.error_x) == 0:
                continue
            ax.plot(
                a.times,
                a.error_x,
                color=run.color.name(),
                label=run.display_label,
                linewidth=0.8,
            )
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("X Error (m)")
        ax.set_title("X-axis Error over Time")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color="black", linewidth=0.5)

    def _plot_error_y_time(self, ax, runs: list[SimulationRun]) -> None:
        for run in runs:
            a = run.analysis
            if len(a.error_y) == 0:
                continue
            ax.plot(
                a.times,
                a.error_y,
                color=run.color.name(),
                label=run.display_label,
                linewidth=0.8,
            )
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Y Error (m)")
        ax.set_title("Y-axis Error over Time")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.axhline(y=0, color="black", linewidth=0.5)

    # ── Export ──

    def _export_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        import csv

        visible = self._selected_runs()
        if not visible:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Error Data", "error_analysis.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)

            header = ["time"]
            for run in visible:
                prefix = run.display_label
                header.extend(
                    [f"{prefix}_error", f"{prefix}_error_x", f"{prefix}_error_y"]
                )
            writer.writerow(header)

            max_len = max((len(r.analysis.errors) for r in visible), default=0)

            for i in range(max_len):
                row = []
                t = ""
                for run in visible:
                    a = run.analysis
                    if i < len(a.times):
                        t = f"{a.times[i]:.4f}"
                        break
                row.append(t)

                for run in visible:
                    a = run.analysis
                    if i < len(a.errors):
                        row.append(f"{a.errors[i]:.6f}")
                        row.append(f"{a.error_x[i]:.6f}")
                        row.append(f"{a.error_y[i]:.6f}")
                    else:
                        row.extend(["", "", ""])
                writer.writerow(row)
