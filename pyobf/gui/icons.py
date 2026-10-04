from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtCore import Qt
from PySide6.QtSvg import QSvgRenderer


PATHS = {
    "open": '<path d="M3 7h6l2 2h10v11H3z"/><path d="M3 7V4h6l2 3h8v2"/>',
    "project": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 9v11"/>',
    "project_run": '<path d="M3 7V4h6l2 3h10v13H3zM8 13h9M13 10l4 3-4 3"/>',
    "run": '<path d="M4 12h15M13 5l7 7-7 7"/>',
    "play": '<path d="M8 4l12 8-12 8z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    "copy": '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    "save": '<path d="M4 3h13l4 4v14H3V3z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
    "settings": '<path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"/><circle cx="12" cy="12" r="3"/>',
    "clear": '<path d="M4 7h16M9 3h6l1 4M6 7l1 14h10l1-14M10 10v7M14 10v7"/>',
    "sidebar": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
}


def make_icon(name, color="#667693"):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="' + color + '" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + PATHS[name] + '</g></svg>'
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
