from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from .i18n import tr
from .designs import DESIGNS
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


class DesignTile(QAbstractButton):
    def __init__(self, key, theme, language, parent=None):
        super().__init__(parent)
        self.key, self.theme, self.language = key, theme, language
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(164, 152)
        self.setAccessibleName(tr(key, language))

    def paintEvent(self, event):
        t = THEMES[self.theme]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.accent if self.isChecked() else t.border), 2 if self.isChecked() else 1))
        p.setBrush(QColor(t.background))
        p.drawRoundedRect(QRectF(2, 2, 160, 110), 14, 14)
        p.setPen(Qt.PenStyle.NoPen)

        def box(x, y, w, h, radius=3, color=None):
            p.setBrush(QColor(color or t.surface))
            p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

        def dot(x, y, radius=3):
            p.setBrush(QColor(t.accent))
            p.drawEllipse(QRectF(x, y, radius * 2, radius * 2))

        if self.key == 'studio':
            for x in (116, 128, 140):
                box(x, 12, 8, 8, color=t.accent)
            box(12, 28, 28, 72)
            regions = [(46, 28, 49, 45), (101, 28, 49, 45)]
            box(46, 79, 104, 21)
        elif self.key == 'focus':
            box(12, 12, 16, 88, 6)
            for y in (20, 35, 50, 65):
                box(16, y, 8, 8, 2, t.accent)
            box(34, 12, 25, 88)
            regions = [(65, 12, 49, 41), (65, 59, 49, 41)]
            box(120, 12, 30, 88)
        else:
            regions = [(12, 12, 51, 52), (69, 12, 51, 52)]
            box(126, 12, 24, 69, 10)
            box(12, 70, 108, 11, 5)
            box(35, 86, 94, 18, 9)
            for x in (46, 64, 100, 118):
                dot(x, 93, 2)
            dot(80, 89, 6)
        for x, y, w, h in regions:
            box(x, y, w, h, 11 if self.key == 'orbit' else 3)
            for line, color in enumerate((t.keyword, t.string, t.number)):
                box(x + 7, y + 10 + line * 8, w - 14 - line * 5, 2, 1, color)
        p.setPen(self.palette().windowText().color())
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        p.setFont(font)
        p.drawText(QRectF(2, 120, 160, 24), Qt.AlignmentFlag.AlignCenter, tr(self.key, self.language))


class PreferencesDialog(QDialog):
    changed = Signal(str, str)
    design_changed = Signal(str)

    def __init__(self, theme, language, parent=None, *, design=None):
        super().__init__(parent)
        self.theme, self.language = theme, language
        self.design = design or getattr(parent, 'design_key', 'studio')
        self.setMinimumWidth(566)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(20)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        colors_page = QWidget()
        colors_layout = QVBoxLayout(colors_page)
        colors_layout.setContentsMargins(8, 16, 8, 8)
        colors_layout.setSpacing(16)
        self.tabs.addTab(colors_page, '')
        self.theme_label = QLabel()
        self.theme_label.setObjectName("subject")
        colors_layout.addWidget(self.theme_label)
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
        colors_layout.addLayout(tiles)
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
        colors_layout.addLayout(language_row)
        design_page = QWidget()
        designs_layout = QHBoxLayout(design_page)
        designs_layout.setContentsMargins(8, 24, 8, 24)
        designs_layout.setSpacing(12)
        self.design_group = QButtonGroup(self)
        self.design_tiles = {}
        for key in DESIGNS:
            tile = DesignTile(key, theme, language)
            tile.setChecked(key == self.design)
            self.design_group.addButton(tile)
            self.design_tiles[key] = tile
            designs_layout.addWidget(tile, 0, Qt.AlignmentFlag.AlignCenter)
        self.design_group.buttonClicked.connect(self.choose_design)
        self.tabs.addTab(design_page, '')
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
        self.tabs.setTabText(0, tr('colors', self.language))
        self.tabs.setTabText(1, tr('design', self.language))
        self.setPalette(palette(THEMES[self.theme]))
        self.setStyleSheet(stylesheet(THEMES[self.theme], self.design))
        for key, tile in self.tiles.items():
            tile.language = self.language
            tile.setAccessibleName(tr(key, self.language))
            tile.update()
        for key, tile in self.design_tiles.items():
            tile.theme, tile.language = self.theme, self.language
            tile.setAccessibleName(tr(key, self.language))
            tile.update()

    def choose_design(self, tile):
        self.design = tile.key
        self.design_changed.emit(self.design)
        self.refresh()

    def choose_theme(self, tile):
        self.theme = tile.key
        self.changed.emit(self.theme, self.language)
        self.refresh()

    def choose_language(self, *_):
        self.language = self.language_combo.currentData()
        self.changed.emit(self.theme, self.language)
        self.refresh()
