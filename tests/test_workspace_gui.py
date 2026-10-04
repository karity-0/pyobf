import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, Qt, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pyobf.gui.app import create_application
from pyobf.gui.preferences import PreferencesDialog
from pyobf.gui.settings import SettingsStore
from pyobf.gui.themes import THEMES
from pyobf.gui.designs import DESIGNS
from pyobf.gui.window import MainWindow
from pyobf import Pipeline


class WorkspaceGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or create_application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = SettingsStore(self.root / "preferences.json")
        self.window = MainWindow(self.store)

    def wait_until(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(condition(), "GUI operation timed out")

    def wait_finished(self):
        self.wait_until(lambda: self.window._thread is None and not self.window.runner.running)
        self.app.processEvents()

    def tearDown(self):
        self.window.runner.stop()
        self.wait_finished()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_theme_preview_and_language_changes_persist(self):
        dialog = PreferencesDialog("white", "ko", self.window)
        dialog.changed.connect(self.window.set_preferences)
        for key in THEMES:
            dialog.tiles[key].click()
            self.assertEqual(self.window.theme_key, key)
            self.assertEqual(self.window.input.highlighter.theme, THEMES[key])
        dialog.language_combo.setCurrentIndex(0)
        self.assertEqual(self.window.language, "en")
        self.assertEqual(self.window.run_button.defaultAction().text(), "Obfuscate Script")
        self.assertEqual(dialog.windowTitle(), "Preferences")
        saved = SettingsStore(self.store.path)
        self.assertEqual((saved.data["theme"], saved.data["language"]), ("crimson", "en"))
        self.assertIn('@{"secret"}', self.window.input.placeholderText())
        dialog.close()

    def test_recent_files_and_projects_can_be_reopened(self):
        project = self.root / "프로젝트"
        project.mkdir()
        first = project / "first.py"
        second = project / "second.py"
        first.write_text('print("first")', encoding="utf-8")
        second.write_text('print("second")', encoding="utf-8")
        self.assertTrue(self.window.load_project(project))
        self.assertTrue(self.window.load_file(first))
        self.assertTrue(self.window.load_file(second))
        self.window._recent_file(self.window.recent_files.item(1))
        self.assertEqual(self.window.input.toPlainText(), 'print("first")')
        reloaded = SettingsStore(self.store.path)
        self.assertEqual(reloaded.data["recent_files"][0], str(first.resolve()))
        self.assertEqual(reloaded.data["recent_projects"], [str(project.resolve())])
        self.window._project = None
        self.window._recent_project(self.window.recent_projects.item(0))
        self.assertEqual(self.window._project, project.resolve())
        self.assertEqual(self.window.tree.rootIndex(), self.window.file_model.index(str(project)))

    def test_design_switch_preserves_work_and_persists_independently_of_colors(self):
        self.window.input.setPlainText('print(123)')
        self.window.process_source()
        self.wait_finished()
        result = self.window._result
        protected = self.window.output.toPlainText()
        self.window.console.setPlainText('previous execution')
        cursor = self.window.input.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
        self.window.input.setTextCursor(cursor)
        dialog = PreferencesDialog('white', 'ko', self.window)
        dialog.changed.connect(self.window.set_preferences)
        dialog.design_changed.connect(self.window.set_design)
        dialog.tabs.setCurrentIndex(1)
        self.window.resize(960, 620)
        self.window.show()
        for design in (*DESIGNS, 'focus', 'orbit'):
            dialog.design_tiles[design].click()
            self.app.processEvents()
            self.assertEqual(self.window.design_key, design)
            self.assertIs(self.window._result, result)
            self.assertEqual(self.window.output.toPlainText(), protected)
            self.assertEqual(self.window.input.textCursor().selectedText(), 'int(1')
            self.assertEqual(self.window.console.toPlainText(), 'previous execution')
            for editor in (self.window.input, self.window.output):
                self.assertGreater(editor.width(), 200)
                self.assertGreater(editor.height(), 80)
            self.assertTrue(self.window.run_button.isEnabled())
            self.assertEqual(SettingsStore(self.store.path).data['design'], design)
        dialog.language_combo.setCurrentIndex(0)
        dialog.tiles['ocean'].click()
        self.assertEqual(dialog.tabs.tabText(1), 'Design')
        self.assertEqual(dialog.design_tiles['orbit'].accessibleName(), 'Orbit')
        saved = SettingsStore(self.store.path)
        self.assertEqual((saved.data['design'], saved.data['theme'], saved.data['language']), ('orbit', 'ocean', 'en'))
        restored = MainWindow(saved)
        self.assertEqual(restored.design_key, 'orbit')
        restored.close()
        restored.deleteLater()
        dialog.close()

    def test_design_switch_while_python_waits_for_input(self):
        self.window.input.setPlainText('print("ready", flush=True)\nprint(input())')
        self.window.input_play_button.click()
        self.wait_until(lambda: 'ready' in self.window.console.toPlainText())
        for design in DESIGNS:
            self.window.set_design(design)
            self.app.processEvents()
            self.assertTrue(self.window.runner.running)
        self.window.stdin.setText('preserved')
        self.window.send_stdin()
        self.wait_finished()
        self.assertIn('preserved', self.window.console.toPlainText())

    def test_project_build_uses_worker_and_preserves_current_editor_result(self):
        project = self.root / 'project'
        project.mkdir()
        source = project / 'main.py'
        source.write_text('print("disk")', encoding='utf-8')
        (project / 'helper.py').write_text('public_value = 24', encoding='utf-8')
        (project / 'resource.txt').write_text('resource', encoding='utf-8')
        self.window.load_project(project)
        self.window.load_file(source)
        self.window.input.setPlainText('print("buffer")')
        self.window.process_source()
        self.wait_finished()
        previous = self.window._result
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(5)
        original_run = Pipeline.run
        def slow_run(pipeline, document):
            self.assertNotEqual(QThread.currentThread(), self.app.thread())
            time.sleep(0.08)
            return original_run(pipeline, document)
        try:
            with patch('pyobf.project.Pipeline.run', slow_run), patch('pyobf.gui.window.QFileDialog.getExistingDirectory', return_value=str(self.root)):
                self.window.project_run_button.click()
                self.assertFalse(self.window.run_button.isEnabled())
                self.assertFalse(self.window.project_run_button.isEnabled())
                self.assertTrue(self.window.stop_button.isEnabled())
                self.window.set_design('orbit')
                self.wait_finished()
        finally:
            timer.stop()
        result = self.window._project_result
        self.assertGreater(len(ticks), 2)
        self.assertEqual(result.scripts, 2)
        self.assertEqual(result.resources, 1)
        self.assertIs(self.window._result, previous)
        self.assertEqual(self.window.output.toPlainText(), previous.source.text)
        self.assertEqual(self.window.input.toPlainText(), 'print("buffer")')
        self.assertEqual(source.read_text(encoding='utf-8'), 'print("disk")')
        self.assertIn('buffer', (result.output / 'main.py').read_text(encoding='utf-8'))
        self.assertIn(str(result.output), self.window.console.toPlainText())
        self.assertTrue(self.window.run_button.isEnabled())

    def test_project_button_selects_source_and_output_when_no_project_is_open(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'main.py').write_text('value = 24', encoding='utf-8')
        self.window.set_preferences('white', 'en')
        self.assertEqual(self.window.project_run_button.defaultAction().text(), 'Obfuscate Project')
        with patch('pyobf.gui.window.QFileDialog.getExistingDirectory', side_effect=[str(project), str(self.root)]) as chooser:
            self.window.project_run_button.click()
            self.wait_finished()
        self.assertEqual(chooser.call_count, 2)
        self.assertEqual(self.window._project, project)
        self.assertTrue((self.window._project_result.output / 'main.py').is_file())

    def test_project_cancellation_and_errors_do_not_publish_partial_output(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'main.py').write_text('value = 24', encoding='utf-8')
        self.window.load_project(project)
        original_run = Pipeline.run
        def slow_run(pipeline, document):
            time.sleep(0.1)
            return original_run(pipeline, document)
        with patch('pyobf.project.Pipeline.run', slow_run):
            self.window.start_project_build(self.root)
            self.window.stop_button.click()
            self.wait_finished()
        self.assertIsNone(self.window._project_result)
        self.assertEqual(self.window._status_key, 'project_cancelled')
        self.assertFalse(list(self.root.glob('project.obf*')))
        (project / 'bad.py').write_text('x = (', encoding='utf-8')
        with patch('pyobf.gui.window.QMessageBox.warning') as warning:
            self.window.start_project_build(self.root)
            self.wait_finished()
        warning.assert_called_once()
        self.assertIn('bad.py', self.window.console.toPlainText())
        self.assertFalse(list(self.root.glob('project.obf*')))
        self.assertFalse(list(self.root.glob('.pyobf-*')))
        self.assertTrue(self.window.project_run_button.isEnabled())

    def test_recent_history_is_deduplicated_and_bounded(self):
        for index in range(12):
            self.store.remember("file", self.root / f"{index}.py")
        self.store.remember("file", self.root / "10.py")
        history = SettingsStore(self.store.path).data["recent_files"]
        self.assertEqual(len(history), 8)
        self.assertEqual(history[0], str((self.root / "10.py").resolve()))
        self.assertEqual(len(set(history)), 8)

    def test_invalid_preferences_fall_back_without_crashing(self):
        self.store.path.write_text('{"theme":"missing","language":"zz","design":"missing","recent_files":[3,"valid.py"]}', encoding="utf-8")
        restored = SettingsStore(self.store.path)
        self.assertEqual(restored.data["theme"], "white")
        self.assertEqual(restored.data["language"], "ko")
        self.assertEqual(restored.data["design"], "studio")
        self.assertEqual(restored.data["recent_files"], ["valid.py"])
        self.store.path.write_text("not json", encoding="utf-8")
        self.assertEqual(SettingsStore(self.store.path).data["recent_files"], [])

    def test_highlighter_respects_strings_comments_and_multiline_state(self):
        editor = self.window.input
        editor.setPlainText('def f():\n    text = "# if 12 🙂"\n    # return 3\n    x = """first\n    if second\n    end"""\n    return 4')
        editor.highlighter.rehighlight()
        blocks = [editor.document().findBlockByNumber(index) for index in range(7)]
        self.assertEqual(blocks[3].userState(), 2)
        self.assertEqual(blocks[4].userState(), 2)
        self.assertEqual(blocks[5].userState(), 0)
        colors = [span.format.foreground().color().name() for span in blocks[2].layout().formats()]
        self.assertEqual(set(colors), {self.window.theme.comment})
        self.assertTrue(any(span.format.foreground().color().name() == self.window.theme.keyword for span in blocks[0].layout().formats()))
        editor.setPlainText('x = "🙂"; return 3')
        editor.highlighter.rehighlight()
        block = editor.document().firstBlock()
        keyword_offset = len('x = "🙂"; '.encode("utf-16-le")) // 2
        self.assertTrue(any(span.start == keyword_offset and span.format.foreground().color().name() == self.window.theme.keyword for span in block.layout().formats()))

    def test_editor_auto_indent_and_line_numbers(self):
        editor = self.window.input
        editor.setPlainText("if True:")
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(editor.toPlainText(), "if True:\n    ")
        QTest.keyClick(editor, Qt.Key.Key_Backtab)
        self.assertEqual(editor.toPlainText(), "if True:\n")
        self.assertEqual(editor.blockCount(), 2)

    def test_run_input_and_output_with_unicode_stdout_and_stderr(self):
        self.window.input.setPlainText('import sys\nprint(@{"안녕 🙂"})\nprint("error-channel", file=sys.stderr)')
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertIn("안녕 🙂", self.window.console.toPlainText())
        self.assertIn("error-channel", self.window.console.toPlainText())
        self.assertIn("종료 코드 0", self.window.status.text())
        self.assertNotIn("안녕", self.window.output.toPlainText())
        self.window.output_play_button.click()
        self.wait_finished()
        self.assertIn("안녕 🙂", self.window.console.toPlainText())
        self.assertTrue(self.window.input_play_button.isEnabled())
        self.assertIsNone(self.window.runner._temporary)

    def test_runtime_errors_go_to_console(self):
        self.window.input.setPlainText('raise RuntimeError("boom")')
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertIn("RuntimeError: boom", self.window.console.toPlainText())
        self.assertIn("종료 코드 1", self.window.status.text())
        self.assertTrue(self.window.run_button.isEnabled())

    def test_stdin_and_stop(self):
        self.window.input.setPlainText('name = input("name? ")\nprint("hello", name)')
        self.window.input_play_button.click()
        self.wait_until(lambda: "name?" in self.window.console.toPlainText())
        self.window.stdin.setText("테스트")
        self.window.stdin.returnPressed.emit()
        self.wait_finished()
        self.assertIn("hello 테스트", self.window.console.toPlainText())
        self.window.input.setPlainText("while True: pass")
        self.window.input_play_button.click()
        self.wait_until(lambda: self.window.runner.process.state() == QProcess.ProcessState.Running)
        self.window.stop_button.click()
        self.wait_finished()
        self.assertEqual(self.window.status.text(), "중지됨")
        self.assertTrue(self.window.open_button.isEnabled())

    def test_project_imports_and_relative_resources(self):
        (self.root / "helper.py").write_text("VALUE = 17", encoding="utf-8")
        (self.root / "resource.txt").write_text("local-resource", encoding="utf-8")
        entry = self.root / "entry.py"
        entry.write_text('from helper import VALUE\nfrom pathlib import Path\nprint(VALUE, Path("resource.txt").read_text())', encoding="utf-8")
        self.window.load_project(self.root)
        self.window.load_file(entry)
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertIn("17 local-resource", self.window.console.toPlainText())

    def test_input_save_preserves_bom_and_crlf(self):
        path = self.root / "source.py"
        path.write_bytes(b'\xef\xbb\xbfprint("old")\r\n')
        self.window.load_file(path)
        self.window.input.setPlainText('print("new")\n')
        self.window.input_save_button.click()
        self.assertEqual(path.read_bytes(), b'\xef\xbb\xbfprint("new")\r\n')

    def test_execution_preserves_filename_and_main_module(self):
        entry = self.root / "entry.py"
        entry.write_text('import __main__\nfrom pathlib import Path\nprint(Path(__file__).name, __main__.__file__ == __file__)\ndef f(): pass\nprint(__main__.f is f)\n', encoding="utf-8")
        self.window.load_file(entry)
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertIn("entry.py True", self.window.console.toPlainText())
        self.assertIn("\nTrue\n", self.window.console.toPlainText())

    def test_integrity_output_runs_from_snapshot_and_saved_file(self):
        entry = self.root / 'integrity.py'
        entry.write_text('@protect_start(integrity, cff, bcf, proxy, morph)\nvalue = 24\nfor i in range(3):\n    value += i\nprint(value, @{"sealed"})\n@protect_end\n', encoding='utf-8')
        self.window.load_file(entry)
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertIn('27 sealed', self.window.console.toPlainText())
        self.assertIn('종료 코드 0', self.window.status.text())
        self.assertEqual(self.window._result.applied_passes[-1], 'IntegrityPass')
        self.window.output_play_button.click()
        self.wait_finished()
        self.assertIn('27 sealed', self.window.console.toPlainText())
        self.assertIn('종료 코드 0', self.window.status.text())

    def test_selected_lines_indent_and_dedent(self):
        editor = self.window.input
        editor.setPlainText("x = 1\ny = 2")
        editor.selectAll()
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        self.assertEqual(editor.toPlainText(), "    x = 1\n    y = 2")
        editor.selectAll()
        QTest.keyClick(editor, Qt.Key.Key_Backtab)
        self.assertEqual(editor.toPlainText(), "x = 1\ny = 2")

    def test_macro_diagnostics_follow_language_setting(self):
        self.window.set_preferences("white", "en")
        with patch("pyobf.gui.window.QMessageBox.warning") as warning:
            self.window._show_error("@{...}에는 문자열 리터럴만 넣을 수 있습니다")
        self.assertEqual(warning.call_args.args[1], "Build failed")
        self.assertEqual(warning.call_args.args[2], "@{...} accepts string literals only")

    def test_theme_and_rehighlight_preserve_output_and_status(self):
        self.window.input.setPlainText('print(@{"preserved"})')
        self.window.input_play_button.click()
        self.wait_finished()
        result = self.window._result
        for theme in THEMES:
            self.window.set_preferences(theme, "en")
            self.window.input.highlighter.rehighlight()
            self.app.processEvents()
            self.assertIs(self.window._result, result)
            self.assertEqual(self.window.output.toPlainText(), result.source.text)
            self.assertIn("exit 0", self.window.status.text())
            self.assertIn("preserved", self.window.console.toPlainText())
            self.assertTrue(self.window.output_play_button.isEnabled())

    def test_failed_process_start_recovers_controls(self):
        self.window.input.setPlainText("x = 1")
        with patch("pyobf.gui.runner.sys.executable", str(self.root / "missing-python.exe")):
            self.window.input_play_button.click()
            self.wait_finished()
        self.assertEqual(self.window._status_key, "run_failed")
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertIsNone(self.window.runner._temporary)

    def test_console_output_is_bounded(self):
        self.window.input.setPlainText('print("x" * 400000)')
        self.window.input_play_button.click()
        self.wait_finished()
        self.assertLessEqual(self.window.console.document().characterCount(), 300001)
        self.assertIn("종료 코드 0", self.window.console.toPlainText())


if __name__ == "__main__":
    unittest.main()
