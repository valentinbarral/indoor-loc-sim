from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(self, params: dict[str, float], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        rss_group = QGroupBox("RSS Log-Distance Path-Loss Model")
        form = QFormLayout()

        self._spin_rssi_at_ref = QDoubleSpinBox()
        self._spin_rssi_at_ref.setRange(-120.0, 0.0)
        self._spin_rssi_at_ref.setValue(params.get("rssi_at_ref", -59.0))
        self._spin_rssi_at_ref.setSingleStep(1.0)
        self._spin_rssi_at_ref.setDecimals(1)
        self._spin_rssi_at_ref.setSuffix(" dBm")
        self._spin_rssi_at_ref.setToolTip(
            "RSSI at reference distance d\u2080 (parameter A). Typical BLE: \u221259 dBm"
        )
        form.addRow("A (RSSI at d\u2080):", self._spin_rssi_at_ref)

        self._spin_d0 = QDoubleSpinBox()
        self._spin_d0.setRange(0.1, 10.0)
        self._spin_d0.setValue(params.get("d0", 1.0))
        self._spin_d0.setSingleStep(0.1)
        self._spin_d0.setDecimals(1)
        self._spin_d0.setSuffix(" m")
        self._spin_d0.setToolTip("Reference distance for the path-loss model")
        form.addRow("d\u2080 (ref. distance):", self._spin_d0)

        self._spin_path_loss = QDoubleSpinBox()
        self._spin_path_loss.setRange(1.0, 6.0)
        self._spin_path_loss.setValue(params.get("path_loss_exponent", 2.0))
        self._spin_path_loss.setSingleStep(0.1)
        self._spin_path_loss.setDecimals(1)
        self._spin_path_loss.setToolTip(
            "Path-loss exponent (n). Free space: 2.0, soft indoor: 2.2\u20133.0, hard indoor: 3.0\u20134.5"
        )
        form.addRow("n (path-loss exp.):", self._spin_path_loss)

        self._spin_sigma = QDoubleSpinBox()
        self._spin_sigma.setRange(0.0, 20.0)
        self._spin_sigma.setValue(params.get("rss_sigma", 2.0))
        self._spin_sigma.setSingleStep(0.5)
        self._spin_sigma.setDecimals(1)
        self._spin_sigma.setSuffix(" dB")
        self._spin_sigma.setToolTip(
            "Shadowing noise std. dev. (\u03c3). Typical: 2\u20136 dB"
        )
        form.addRow("\u03c3 (shadowing noise):", self._spin_sigma)

        self._spin_wall_att = QDoubleSpinBox()
        self._spin_wall_att.setRange(0.0, 30.0)
        self._spin_wall_att.setValue(params.get("wall_attenuation_db", 3.0))
        self._spin_wall_att.setSingleStep(0.5)
        self._spin_wall_att.setDecimals(1)
        self._spin_wall_att.setSuffix(" dB")
        self._spin_wall_att.setToolTip(
            "Attenuation per wall crossing. Light wall: 3\u20135 dB, thick wall: 8\u201315 dB"
        )
        form.addRow("Wall attenuation:", self._spin_wall_att)

        rss_group.setLayout(form)
        layout.addWidget(rss_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_params(self) -> dict[str, float]:
        return {
            "rssi_at_ref": self._spin_rssi_at_ref.value(),
            "d0": self._spin_d0.value(),
            "path_loss_exponent": self._spin_path_loss.value(),
            "rss_sigma": self._spin_sigma.value(),
            "wall_attenuation_db": self._spin_wall_att.value(),
        }
