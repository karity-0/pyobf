import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from .. import __version__
from .themes import THEMES, palette
from .window import MainWindow


def create_application() -> QApplication:
    app = QApplication(sys.argv)
    app.setApplicationName("pyobf")
    app.setOrganizationName("pyobf")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setPalette(palette(THEMES["white"]))
    return app


def main() -> int:
    app = create_application()
    window = MainWindow()
    window.show()
    return app.exec()
