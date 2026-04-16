from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from indoor_loc_sim.gui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Indoor Localization Simulator")
    app.setOrganizationName("IndoorLocSim")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
