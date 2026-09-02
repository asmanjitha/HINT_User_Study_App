#!/usr/bin/env python3
"""HINT Study Console entry point.

    python main.py

Reads config/app.yaml + config/study.yaml, sets up logging, opens the
databases, and shows the main Researcher Console window.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from core.application_controller import ApplicationController
from core.config_loader import DEFAULT_CONFIG_DIR, PROJECT_ROOT
from gui.main_window import MainWindow
from gui.storage_location_dialog import ensure_storage_location

logger = logging.getLogger(__name__)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HINT Study Console")
    app.setOrganizationName("HINT Study")

    if ensure_storage_location(DEFAULT_CONFIG_DIR, PROJECT_ROOT) is None:
        return 0

    controller = ApplicationController()

    window = MainWindow(controller)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
