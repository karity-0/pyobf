from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .i18n import tr
from .themes import THEMES, palette, stylesheet
from .. import __version__


class ThemeTile(QAbstractButton):
    def __init__(self, key, language, parent=None):
        super().__init__(parent)
        self.key, self.language = key, language
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(124, 112))
        self.setAccessibleName(tr(key, language))

    def paintEvent(self, event):
        t = THEMES[self.key]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t.accent if self.isChecked() else t.border), 2 if self.isChecked() else 1))
        if t.gradient:
            gradient = QLinearGradient(2, 2, 122, 80)
            gradient.setColorAt(0, QColor(t.background))
            gradient.setColorAt(1, QColor(t.gradient))
            painter.setBrush(gradient)
        else:
            painter.setBrush(QColor(t.background))
        painter.drawRoundedRect(QRectF(2, 2, 120, 78), 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.surface))
        painter.drawRoundedRect(QRectF(10, 20, 104, 51), 5, 5)
        for index, color in enumerate((t.accent, t.string, t.number)):
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(11 + index * 8, 10, 4, 4))
        painter.setBrush(QColor(t.raised))
        painter.drawRect(QRectF(10, 20, 22, 51))
        for y, width, color in ((29, 27, t.keyword), (39, 56, t.string), (49, 38, t.number), (59, 47, t.muted)):
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(39, y, width, 3), 1, 1)
        painter.setPen(self.palette().windowText().color())
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(QRectF(2, 86, 120, 24), Qt.AlignmentFlag.AlignCenter, tr(self.key, self.language))


class PreferencesDialog(QDialog):
    changed = Signal(str, str)

    def __init__(self, theme, language, parent=None):
        super().__init__(parent)
        self.theme, self.language = theme, language
        self.setMinimumWidth(566)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(20)
        self.theme_label = QLabel()
        self.theme_label.setObjectName("subject")
        layout.addWidget(self.theme_label)
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.group = QButtonGroup(self)
        self.tiles = {}
        for index, key in enumerate(THEMES):
            tile = ThemeTile(key, language)
            tile.setChecked(key == theme)
            self.group.addButton(tile)
            tiles.addWidget(tile, index // 4, index % 4)
            self.tiles[key] = tile
        self.group.buttonClicked.connect(self.choose_theme)
        layout.addLayout(tiles)
        language_row = QHBoxLayout()
        self.language_label = QLabel()
        self.language_label.setObjectName("subject")
        language_row.addWidget(self.language_label)
        language_row.addStretch()
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("한국어 / Korean", "ko")
        self.language_combo.setCurrentIndex(0 if language == "en" else 1)
        self.language_combo.currentIndexChanged.connect(self.choose_language)
        language_row.addWidget(self.language_combo)
        layout.addLayout(language_row)
        bottom = QHBoxLayout()
        version = QLabel("pyobf  ·  " + __version__)
        version.setObjectName("muted")
        bottom.addWidget(version)
        bottom.addStretch()
        self.done_button = QPushButton()
        self.done_button.clicked.connect(self.accept)
        bottom.addWidget(self.done_button)
        layout.addLayout(bottom)
        self.refresh()

    def refresh(self):
        self.setWindowTitle(tr("preferences", self.language))
        self.theme_label.setText(tr("theme", self.language))
        self.language_label.setText(tr("language", self.language))
        self.done_button.setText(tr("done", self.language))
        self.setPalette(palette(THEMES[self.theme]))
        self.setStyleSheet(stylesheet(THEMES[self.theme]))
        for key, tile in self.tiles.items():
            tile.language = self.language
            tile.setAccessibleName(tr(key, self.language))
            tile.update()

    def choose_theme(self, tile):
        self.theme = tile.key
        self.changed.emit(self.theme, self.language)
        self.refresh()

    def choose_language(self, *_):
        self.language = self.language_combo.currentData()
        self.changed.emit(self.theme, self.language)
        self.refresh()
