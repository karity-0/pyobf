from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True)
class Theme:
    background: str
    surface: str
    raised: str
    border: str
    text: str
    muted: str
    accent: str
    selection: str
    line: str
    keyword: str
    string: str
    number: str
    comment: str
    error: str
    gradient: str = ""


THEMES = {
    "white": Theme("#f0f1f3", "#ffffff", "#f6f7f8", "#dce0e5", "#242930", "#737b87", "#434e60", "#e0e5ec", "#f2f4f7", "#385fbd", "#23826c", "#ac622d", "#8d949f", "#c84860"),
    "dark": Theme("#151820", "#1e222d", "#262c39", "#32394a", "#dce3f1", "#8591aa", "#aa9bff", "#3d3c61", "#252b3b", "#c5a4ff", "#9ed4ae", "#edbc82", "#6d7d98", "#ff829b"),
    "crystal": Theme("#ded5fa", "#f5f0ff", "#e7dcfa", "#c4b1e3", "#403356", "#7a6594", "#8854c6", "#d7bdf4", "#eae0fb", "#9250bd", "#158a88", "#bb6395", "#9685ac", "#ce4f85", "#bfeee9"),
    "ocean": Theme("#092032", "#102c42", "#17394f", "#24485e", "#d5edf5", "#7fa8bb", "#48d5cb", "#235363", "#173b50", "#86bcff", "#7ed9be", "#f2c581", "#598ba2", "#ff98a9", "#0c2c43"),
    "dracula": Theme("#21222c", "#282a36", "#343746", "#45485c", "#f8f8f2", "#939fc8", "#bd93f9", "#504263", "#343442", "#ff79c6", "#f1fa8c", "#bd93f9", "#7787b8", "#ff5555", "#30283f"),
    "mythic": Theme("#eadbb7", "#fff9ea", "#f3e3bc", "#d7bc7c", "#574322", "#8e7344", "#9b742a", "#ead29a", "#f8ecd0", "#a27524", "#697f3e", "#be6833", "#aa9669", "#bb5752", "#fcf3db"),
    "crimson": Theme("#211319", "#2d1b23", "#3a242e", "#55303d", "#f6dfe6", "#b08797", "#f16a89", "#663448", "#3b222e", "#ff91ae", "#e8c38d", "#ff9a7c", "#aa7187", "#ff546e", "#341823"),
}


def palette(theme: Theme) -> QPalette:
    result = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, theme.background), (QPalette.ColorRole.WindowText, theme.text),
        (QPalette.ColorRole.Base, theme.surface), (QPalette.ColorRole.AlternateBase, theme.raised),
        (QPalette.ColorRole.Text, theme.text), (QPalette.ColorRole.Button, theme.raised),
        (QPalette.ColorRole.ButtonText, theme.text), (QPalette.ColorRole.Highlight, theme.selection),
        (QPalette.ColorRole.HighlightedText, theme.text), (QPalette.ColorRole.ToolTipBase, theme.surface),
        (QPalette.ColorRole.ToolTipText, theme.text), (QPalette.ColorRole.PlaceholderText, theme.muted),
    ):
        result.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(theme.muted))
    return result


def stylesheet(t: Theme, design='studio') -> str:
    from .designs import design_stylesheet
    background = t.background if not t.gradient else f"qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {t.background},stop:1 {t.gradient})"
    return f"""
    QMainWindow, QDialog, QWidget#root {{ background: {background}; color: {t.text}; }}
    QWidget {{ color: {t.text}; font-size: 12px; }}
    QLabel {{ background: transparent; }}
    QLabel#brand {{ font-size: 24px; font-weight: 700; letter-spacing: -1px; }}
    QLabel#version {{ color: {t.muted}; background: {t.raised}; border: 1px solid {t.border}; border-radius: 7px; padding: 3px 8px; }}
    QLabel#section {{ color: {t.muted}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
    QLabel#muted {{ color: {t.muted}; }}
    QLabel#subject {{ color: {t.text}; font-weight: 600; font-size: 12px; }}
    QFrame#card, QFrame#sidebar {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    QFrame#console {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    QPlainTextEdit, QTreeView, QListWidget {{ background: {t.surface}; color: {t.text}; border: none; selection-background-color: {t.selection}; selection-color: {t.text}; }}
    QPlainTextEdit {{ padding: 5px; font-size: 13px; }}
    QTreeView, QListWidget {{ font-size: 12px; outline: none; }}
    QTreeView::item, QListWidget::item {{ height: 30px; border-radius: 5px; padding: 0px 5px; }}
    QTreeView::item:hover, QListWidget::item:hover {{ background: {t.raised}; }}
    QTreeView::item:selected, QListWidget::item:selected {{ background: {t.selection}; }}
    QToolButton, QPushButton {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 8px; padding: 8px; }}
    QToolButton:hover, QPushButton:hover {{ background: {t.selection}; border-color: {t.accent}; }}
    QToolButton:pressed, QPushButton:pressed {{ background: {t.selection}; }}
    QToolButton:disabled, QPushButton:disabled {{ color: {t.muted}; border-color: {t.border}; }}
    QToolButton#primary {{ background: {t.accent}; color: {t.surface}; border: none; padding: 9px 16px; font-weight: 600; }}
    QToolButton#primary:disabled {{ background: {t.raised}; color: {t.muted}; }}
    QLineEdit, QComboBox {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 7px; padding: 8px; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {t.accent}; }}
    QComboBox QAbstractItemView {{ background: {t.surface}; selection-background-color: {t.selection}; }}
    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabBar::tab {{ background: {t.raised}; border: none; border-radius: 9px; padding: 9px 18px; margin-right: 6px; }}
    QTabBar::tab:selected {{ background: {t.accent}; color: {t.surface}; }}
    QTabBar::tab:hover:!selected {{ background: {t.selection}; }}
    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 25px; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 25px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}
    """ + design_stylesheet(design, t)
