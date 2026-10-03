from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QDir, QSize, Qt, QThread, Slot
from PySide6.QtGui import QAction, QColor, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFileSystemModel, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QSplitter, QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from .. import __version__
from ..pipeline import Pipeline, PipelineResult
from ..source import SourceDocument
from .editor import CodeEditor
from .i18n import diagnostic, tr
from .icons import make_icon
from .preferences import PreferencesDialog
from .runner import PythonRunner
from .settings import SettingsStore
from .themes import THEMES, palette, stylesheet


class AnalysisWorker(QThread):
    """Read results only after the native thread has completely finished."""

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = Pipeline().run(self.source)
        except SyntaxError as error:
            self.error = f"{error.filename}:{error.lineno or 1}:{error.offset or 1}\n{error.msg}"
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else SettingsStore()
        self.language = self.settings.data["language"]
        self.theme_key = self.settings.data["theme"]
        self.theme = THEMES[self.theme_key]
        self._loaded = SourceDocument("")
        self._loaded_display = ""
        self._input_text = ""
        self._result = None
        self._thread = None
        self._run_after_build = False
        self._project = None
        self._closing = False
        self._status_key = "ready"
        self._status_values = {}
        self._buttons = []
        self._labels = []
        self.resize(1320, 820)
        self.setMinimumSize(960, 620)
        self.runner = PythonRunner(self)
        self.runner.output.connect(self._append_console)
        self.runner.started.connect(self._execution_started)
        self.runner.finished.connect(self._execution_finished)
        self.runner.failed.connect(self._execution_failed)
        self._create_ui()
        self.input.textChanged.connect(self._input_changed)
        self.apply_appearance()
        self._invalidate()

    def text(self, key, **values):
        return tr(key, self.language, **values)

    def _label(self, key, object_name="section"):
        label = QLabel()
        label.setObjectName(object_name)
        self._labels.append((label, key))
        return label

    def _button(self, icon, key, shortcut, callback, primary=False):
        button = QToolButton()
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        action = QAction(self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            self.addAction(action)
        action.triggered.connect(callback)
        button.setDefaultAction(action)
        if primary:
            button.setObjectName("primary")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._buttons.append((button, icon, key, shortcut, primary))
        return button

    def _create_ui(self):
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 12)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(10)
        self.sidebar_button = self._button("sidebar", "sidebar", "Ctrl+B", self.toggle_sidebar)
        header.addWidget(self.sidebar_button)
        brand = QLabel("pyobf")
        brand.setObjectName("brand")
        header.addWidget(brand)
        version = QLabel(__version__)
        version.setObjectName("version")
        header.addWidget(version)
        header.addStretch()
        self.open_button = self._button("open", "open", "Ctrl+O", self.open_source)
        self.project_button = self._button("project", "project_open", "Ctrl+Shift+O", self.open_project)
        self.run_button = self._button("run", "build", "Ctrl+Return", self.process_source, True)
        self.pref_button = self._button("settings", "preferences", "Ctrl+,", self.open_preferences)
        for button in (self.open_button, self.project_button, self.run_button, self.pref_button):
            header.addWidget(button)
        layout.addLayout(header)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(290)
        nav = QVBoxLayout(self.sidebar)
        nav.setContentsMargins(14, 18, 14, 14)
        nav.setSpacing(10)
        nav.addWidget(self._label("project"))
        self.project_subject = QLabel()
        self.project_subject.setObjectName("subject")
        self.project_subject.hide()
        nav.addWidget(self.project_subject)
        self.project_empty = self._label("no_project", "muted")
        self.project_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.project_empty.setWordWrap(True)
        self.project_empty.setMinimumHeight(100)
        nav.addWidget(self.project_empty, 1)
        self.file_model = QFileSystemModel(self)
        self.file_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.file_model.setNameFilters(["*.py", "*.pyw", "*.pyobf"])
        self.file_model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.file_model)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setAnimated(False)
        for column in (1, 2, 3):
            self.tree.hideColumn(column)
        self.tree.doubleClicked.connect(self._tree_open)
        self.tree.hide()
        nav.addWidget(self.tree, 1)
        nav.addWidget(self._label("recent_files"))
        self.recent_files = QListWidget()
        self.recent_files.setMaximumHeight(172)
        self.recent_files.setMinimumHeight(40)
        self.recent_files.itemClicked.connect(self._recent_file)
        nav.addWidget(self.recent_files)
        nav.addWidget(self._label("recent_projects"))
        self.recent_projects = QListWidget()
        self.recent_projects.setMaximumHeight(130)
        self.recent_projects.setMinimumHeight(40)
        self.recent_projects.itemClicked.connect(self._recent_project)
        nav.addWidget(self.recent_projects)

        self.input, self.output = CodeEditor(), CodeEditor(True)
        self.input_subject, self.output_subject = QLabel(), QLabel()
        self.input_save_button = self._button("save", "save_input", "Ctrl+S", self.save_input)
        self.input_play_button = self._button("play", "run_input", "F5", self.execute_input)
        self.copy_button = self._button("copy", "copy", "Ctrl+Shift+C", self.copy_output)
        self.save_button = self._button("save", "save", "Ctrl+Shift+S", self.save_output)
        self.output_play_button = self._button("play", "run_output", "Shift+F5", self.execute_output)
        editors = QSplitter(Qt.Orientation.Horizontal)
        editors.setHandleWidth(14)
        editors.setChildrenCollapsible(False)
        editors.addWidget(self._panel("input", self.input_subject, self.input, [self.input_save_button, self.input_play_button]))
        editors.addWidget(self._panel("output", self.output_subject, self.output, [self.copy_button, self.save_button, self.output_play_button]))
        editors.setSizes([520, 520])

        console_card = QFrame()
        console_card.setObjectName("console")
        console_card.setMinimumHeight(180)
        console_layout = QVBoxLayout(console_card)
        console_layout.setContentsMargins(14, 8, 14, 10)
        console_layout.setSpacing(5)
        console_header = QHBoxLayout()
        console_header.addWidget(self._label("console"))
        self.execution_label = QLabel()
        self.execution_label.setObjectName("muted")
        console_header.addWidget(self.execution_label)
        console_header.addStretch()
        self.clear_button = self._button("clear", "clear", "", self.clear_console)
        self.stop_button = self._button("stop", "stop", "Shift+F6", self.runner.stop)
        console_header.addWidget(self.clear_button)
        console_header.addWidget(self.stop_button)
        console_layout.addLayout(console_header)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(self.input.font())
        self.console.setMaximumBlockCount(2000)
        console_layout.addWidget(self.console, 1)
        self.stdin = QLineEdit()
        self.stdin.returnPressed.connect(self.send_stdin)
        console_layout.addWidget(self.stdin)
        work = QSplitter(Qt.Orientation.Vertical)
        work.setHandleWidth(14)
        work.setChildrenCollapsible(False)
        work.addWidget(editors)
        work.addWidget(console_card)
        work.setSizes([490, 220])
        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setHandleWidth(16)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self.sidebar)
        workspace.addWidget(work)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([214, 1060])
        layout.addWidget(workspace, 1)
        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        footer.addWidget(self.status)
        footer.addStretch()
        info = QLabel(f"Python {sys.version_info.major}.{sys.version_info.minor}  ·  UTF-8")
        info.setObjectName("muted")
        footer.addWidget(info)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def _panel(self, key, subject, editor, buttons):
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(self._label(key))
        subject.setObjectName("subject")
        titles.addWidget(subject)
        row.addLayout(titles)
        row.addStretch()
        for button in buttons:
            row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(editor, 1)
        return panel

    @staticmethod
    def _enabled(button, enabled):
        button.defaultAction().setEnabled(enabled)
        button.setEnabled(enabled)

    def apply_appearance(self):
        old_theme = self.theme
        self.theme = THEMES[self.theme_key]
        self.setPalette(palette(self.theme))
        QApplication.instance().setPalette(palette(self.theme))
        self.setStyleSheet(stylesheet(self.theme))
        for editor in (self.input, self.output):
            editor.set_theme(self.theme)
        if old_theme != self.theme:
            cursor = self.console.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self.theme.text))
            cursor.setCharFormat(fmt)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.console.setTextCursor(cursor)
        self.retranslate()

    def retranslate(self):
        for label, key in self._labels:
            label.setText(self.text(key))
        for button, icon, key, shortcut, primary in self._buttons:
            title = self.text(key)
            action = button.defaultAction()
            action.setText(title)
            action.setIcon(make_icon(icon, self.theme.surface if primary else self.theme.accent if icon == "play" else self.theme.muted))
            button.setToolTip(title + (f" ({shortcut})" if shortcut else ""))
            button.setAccessibleName(title)
        self.input.setPlaceholderText(self.text("placeholder"))
        self.input.setAccessibleName(self.text("input"))
        self.output.setAccessibleName(self.text("output"))
        self.output.setPlaceholderText("→")
        self.console.setPlaceholderText(self.text("console_hint"))
        self.console.setAccessibleName(self.text("console"))
        self.stdin.setPlaceholderText(self.text("stdin"))
        self.stdin.setAccessibleName(self.text("stdin"))
        self.tree.setAccessibleName(self.text("project"))
        if hasattr(self, "_execution_target"):
            self.execution_label.setText(self.text(self._execution_target))
        self.recent_files.setAccessibleName(self.text("recent_files"))
        self.recent_projects.setAccessibleName(self.text("recent_projects"))
        self._update_subjects()
        self.refresh_recents()
        self._refresh_status()

    def set_preferences(self, theme, language):
        try:
            self.settings.preferences(theme, language)
        except OSError as error:
            QMessageBox.warning(self, self.text("settings_failed"), str(error))
            return
        self.theme_key, self.language = theme, language
        self.apply_appearance()

    @Slot()
    def open_preferences(self):
        dialog = PreferencesDialog(self.theme_key, self.language, self)
        dialog.changed.connect(self.set_preferences)
        dialog.exec()
        dialog.deleteLater()

    @Slot()
    def toggle_sidebar(self):
        self.sidebar.setVisible(self.sidebar.isHidden())

    def refresh_recents(self):
        for widget, key, icon in ((self.recent_files, "recent_files", "open"), (self.recent_projects, "recent_projects", "project")):
            widget.clear()
            for path in self.settings.data[key]:
                item = QListWidgetItem(make_icon(icon, self.theme.muted), Path(path).name or path)
                item.setToolTip(path)
                item.setData(Qt.ItemDataRole.UserRole, path)
                widget.addItem(item)
            if not widget.count():
                item = QListWidgetItem(self.text("no_recents"))
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                widget.addItem(item)

    def _remember(self, kind, path):
        try:
            self.settings.remember(kind, path)
        except OSError as error:
            self._append_console(self.text("settings_failed") + ": " + str(error) + "\n", True)
        self.refresh_recents()

    def _recent_file(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.load_file(path)

    def _recent_project(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.load_project(path)

    def _tree_open(self, index):
        path = Path(self.file_model.filePath(index))
        if path.is_file():
            self.load_file(path)

    def _can_replace(self):
        if self.input.document().isModified() and self.input.toPlainText():
            choice = QMessageBox.question(self, self.text("unsaved"), self.text("discard"), QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            return choice == QMessageBox.StandardButton.Discard
        return True

    @Slot()
    def open_source(self):
        path, _ = QFileDialog.getOpenFileName(self, self.text("open"), str(self.working_directory()), "Python (*.py *.pyw *.pyobf);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self._thread is not None or self.runner.running or not self._can_replace():
            return False
        try:
            document = SourceDocument.read(Path(path).resolve())
        except (OSError, UnicodeError, SyntaxError) as error:
            QMessageBox.warning(self, self.text("open_failed"), str(error))
            return False
        self._loaded = document
        self.input.setPlainText(document.text)
        self._loaded_display = self.input.toPlainText()
        self.input.document().setModified(False)
        self._invalidate()
        self._remember("file", document.filename)
        self._update_subjects()
        self.input.setFocus()
        return True

    @Slot()
    def open_project(self):
        path = QFileDialog.getExistingDirectory(self, self.text("project_open"), str(self.working_directory()))
        if path:
            self.load_project(path)

    def load_project(self, path):
        if self._thread is not None or self.runner.running:
            return False
        path = Path(path).resolve()
        if not path.is_dir():
            QMessageBox.warning(self, self.text("project_failed"), str(path))
            return False
        self._project = path
        self.project_subject.setText(path.name or str(path))
        self.project_subject.setToolTip(str(path))
        self.project_subject.show()
        self.project_empty.hide()
        self.tree.setRootIndex(self.file_model.setRootPath(str(path)))
        self.tree.show()
        self.sidebar.show()
        self._remember("project", path)
        return True

    def _update_subjects(self):
        name = self.text("untitled") if self._loaded.filename.startswith("<") else Path(self._loaded.filename).name
        self.input_subject.setText(name)
        self.input_subject.setToolTip(self._loaded.filename)
        self.output_subject.setText(Path(name).stem + ".obf.py" if self._result else self.text("output_subject"))
        self.setWindowTitle(f"{name} · pyobf {__version__}")

    def _input_source(self):
        text = self.input.toPlainText()
        if text == self._loaded_display:
            return self._loaded
        if "\r\n" in self._loaded.text:
            text = text.replace("\n", "\r\n")
        return self._loaded.with_text(text)

    def working_directory(self):
        if not self._loaded.filename.startswith("<"):
            parent = Path(self._loaded.filename).parent
            if parent.is_dir():
                return parent
        return self._project or Path.cwd()

    @Slot()
    def save_input(self):
        document = self._input_source()
        path = "" if document.filename.startswith("<") else document.filename
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, self.text("save_input"), str(self.working_directory() / "untitled.py"), "Python (*.py *.pyw *.pyobf);;All files (*)")
        if not path:
            return
        try:
            data = document.to_bytes()
            Path(path).write_bytes(data)
            self._loaded = SourceDocument.from_bytes(data, str(Path(path).resolve()))
        except (OSError, UnicodeError, SyntaxError) as error:
            QMessageBox.warning(self, self.text("save_failed"), str(error))
            return
        self._loaded_display = self.input.toPlainText()
        self.input.document().setModified(False)
        self._remember("file", path)
        self._update_subjects()
        self.set_status("saved")

    def _update_controls(self):
        busy = self._thread is not None or self.runner.running
        self.input.setReadOnly(self._thread is not None)
        for button in (self.open_button, self.project_button, self.run_button, self.input_play_button, self.input_save_button):
            self._enabled(button, not busy)
        self.tree.setEnabled(not busy)
        self.recent_files.setEnabled(not busy)
        self.recent_projects.setEnabled(not busy)
        available = self._result is not None
        self._enabled(self.copy_button, available)
        self._enabled(self.save_button, available)
        self._enabled(self.output_play_button, available and not busy)
        self._enabled(self.stop_button, self.runner.running)
        self.stdin.setEnabled(self.runner.running)

    @Slot()
    def _input_changed(self):
        text = self.input.toPlainText()
        if text != self._input_text:
            self._input_text = text
            self._invalidate()

    @Slot()
    def _invalidate(self):
        self._result = None
        self.output.clear()
        if not self.runner.running:
            self.set_status("ready")
        self._update_subjects()
        self._update_controls()

    def set_status(self, key, **values):
        self._status_key, self._status_values = key, values
        self._refresh_status()

    def _refresh_status(self):
        values = self._status_values.copy()
        if self._status_key == "built":
            detail = self.text("changes", count=values["changes"]) if values["changes"] else self.text("unchanged")
            self.status.setText(f"AST {values['nodes']:,} · {detail}")
        elif self._status_key == "running":
            self.status.setText(self.text("running", target=self.text(values["target"])))
        else:
            self.status.setText(self.text(self._status_key, **values))
        self.status.setStyleSheet("color: " + (self.theme.error if self._status_key in ("syntax", "run_failed") else self.theme.muted))

    @Slot()
    def process_source(self):
        self._start_build(False)

    def _start_build(self, execute):
        if self._thread is not None or self.runner.running:
            return
        self._run_after_build = execute
        self._invalidate()
        self.set_status("analyzing")
        self._thread = AnalysisWorker(self._input_source(), self)
        self._thread.finished.connect(self._finish_analysis, Qt.ConnectionType.QueuedConnection)
        self._update_controls()
        self._thread.start()

    @Slot(object)
    def _show_result(self, result):
        self._result = result
        self.output.setPlainText(result.source.text)
        self._update_subjects()
        self.set_status("built", nodes=len(result.analysis.nodes), changes=result.replacement_count)
        self._update_controls()

    @Slot(str)
    def _show_error(self, message):
        self.set_status("syntax")
        QMessageBox.warning(self, self.text("analysis_failed"), diagnostic(message, self.language))

    @Slot()
    def _finish_analysis(self):
        thread = self._thread
        if thread is None:
            return
        execute = self._run_after_build
        self._thread = None
        self._run_after_build = False
        try:
            if thread.result is not None:
                self._show_result(thread.result)
                if execute:
                    self._launch_process(thread.result.source, "input")
            elif thread.error is not None:
                self._show_error(thread.error)
        finally:
            self._update_controls()
            thread.deleteLater()

    @Slot()
    def execute_input(self):
        self._start_build(True)

    @Slot()
    def execute_output(self):
        if self._result is not None and self._thread is None and not self.runner.running:
            self._launch_process(self._result.source, "output")

    def _launch_process(self, document, target):
        self.console.clear()
        self._execution_target = target
        self.execution_label.setText(self.text(target))
        self.set_status("running", target=target)
        try:
            self.runner.start(document, self.working_directory(), self._project)
        except (OSError, UnicodeError) as error:
            self._execution_failed(str(error))
            return
        self._update_controls()

    @Slot()
    def _execution_started(self):
        self._update_controls()

    @Slot(int, bool)
    def _execution_finished(self, code, stopped):
        self.set_status("stopped" if stopped else "finished", code=code)
        self._append_console("\n" + self.status.text() + "\n", False, muted=True)
        self._update_controls()
        if self._closing:
            self.close()

    @Slot(str)
    def _execution_failed(self, message):
        self.set_status("run_failed")
        self._append_console(message + "\n", True)
        self._update_controls()
        if self._closing:
            self.close()

    def _append_console(self, text, error=False, muted=False):
        if len(text) > 32768:
            text = "…\n" + text[-32768:]
        if self.console.document().characterCount() + len(text) > 300000:
            self.console.clear()
            text = "…\n" + text
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.theme.error if error else self.theme.muted if muted else self.theme.text))
        cursor.insertText(text, fmt)
        self.console.setTextCursor(cursor)
        self.console.ensureCursorVisible()

    @Slot()
    def clear_console(self):
        self.console.clear()

    @Slot()
    def send_stdin(self):
        text = self.stdin.text()
        self.runner.send(text)
        self._append_console("> " + text + "\n", muted=True)
        self.stdin.clear()

    @Slot()
    def copy_output(self):
        if self._result is not None:
            QApplication.clipboard().setText(self._result.source.text)
            self.set_status("copied")

    @Slot()
    def save_output(self):
        if self._result is None:
            return
        filename = self._result.source.filename
        suggested = "output.py" if filename.startswith("<") else str(Path(filename).with_name(Path(filename).stem + ".obf" + Path(filename).suffix))
        path, _ = QFileDialog.getSaveFileName(self, self.text("save"), suggested, "Python (*.py *.pyw);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_bytes(self._result.source.to_bytes())
        except (OSError, UnicodeError) as error:
            QMessageBox.warning(self, self.text("save_failed"), str(error))
            return
        self.set_status("saved")

    def closeEvent(self, event):
        if self._thread is not None:
            self.set_status("closing")
            event.ignore()
            return
        if self.runner.running:
            self._closing = True
            self.runner.stop()
            event.ignore()
            return
        super().closeEvent(event)
