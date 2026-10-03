import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pyobf.gui.app import create_application
from pyobf.gui.window import MainWindow
from pyobf.gui.settings import SettingsStore


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or create_application()

    def setUp(self):
        self.settings_directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(SettingsStore(Path(self.settings_directory.name) / "preferences.json"))

    def tearDown(self):
        self.wait_for_worker()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.settings_directory.cleanup()

    def wait_for_worker(self):
        deadline = time.monotonic() + 5
        while self.window._thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            # Explicitly yield the GIL to the Python worker on every iteration.
            time.sleep(0.01)
        self.assertIsNone(self.window._thread, "Background analysis did not finish")

    def test_paste_run_copy_and_invalidate(self):
        code = "이름 = 2\nprint(이름)\n"
        self.window.input.setPlainText(code)
        self.window.run_button.click()
        self.wait_for_worker()
        transformed = self.window.output.toPlainText()
        self.assertNotIn("이름", transformed)
        self.assertIn("StripInfoPass", self.window._result.applied_passes)
        self.window.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), transformed)
        self.window.input.insertPlainText("# changed\n")
        self.assertIsNone(self.window._result)
        self.assertFalse(self.window.save_button.isEnabled())
        self.assertEqual(self.window.output.toPlainText(), "")

    def test_open_and_save_exact_bytes(self):
        data = b"\xef\xbb\xbf# keep\r\nx = 2  \r\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.py"
            output = Path(directory) / "output.py"
            source.write_bytes(data)
            with patch("pyobf.gui.window.QFileDialog.getOpenFileName", return_value=(str(source), "")):
                self.window.open_button.click()
            self.window.run_button.click()
            self.wait_for_worker()
            with patch("pyobf.gui.window.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                self.window.save_button.click()
            self.assertEqual(output.read_bytes(), b"\xef\xbb\xbf\r\nx = 2  \r\n")

    def test_error_clears_old_output(self):
        self.window.input.setPlainText("x = 1")
        self.window.run_button.click()
        self.wait_for_worker()
        self.window.input.setPlainText("x = (")
        with patch("pyobf.gui.window.QMessageBox.warning") as warning:
            self.window.run_button.click()
            self.wait_for_worker()
        warning.assert_called_once()
        self.assertIsNone(self.window._result)
        self.assertFalse(self.window.copy_button.isEnabled())
        self.assertFalse(self.window.input.isReadOnly())

    def test_string_macro_run_copy_and_save(self):
        self.window.input.setPlainText('x = @{"hello world"}\ny = "unchanged"\n')
        self.window.run_button.click()
        self.wait_for_worker()
        result = self.window._result
        self.assertIsNotNone(result)
        self.assertEqual(result.pass_records[0].replacements, 1)
        self.assertIn("StripInfoPass", result.applied_passes)
        self.assertIn(f"변환 {result.replacement_count}곳", self.window.status.text())
        self.assertNotIn("hello world", self.window.output.toPlainText())
        self.assertIn('y = "unchanged"', self.window.output.toPlainText())
        self.window.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), result.source.text)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "encrypted.py"
            with patch("pyobf.gui.window.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                self.window.save_button.click()
            namespace = {}
            exec(output.read_bytes(), namespace)
        self.assertEqual(namespace["x"], "hello world")
        self.assertEqual(namespace["y"], "unchanged")

    def test_protected_region_can_be_processed_in_gui(self):
        code = 'def f(n):\n    @protect_start(cff, junk)\n    x = n + 1\n    x *= 2\n    @protect_end\n    return x\n'
        self.window.input.setPlainText(code)
        self.window.run_button.click()
        self.wait_for_worker()
        self.assertIsNotNone(self.window._result)
        self.assertEqual(self.window._result.applied_passes, ("ProtectionPass", "StripInfoPass"))
        self.assertGreater(self.window._result.replacement_count, 1)
        self.assertNotIn("@protect_", self.window.output.toPlainText())
        namespace = {}
        exec(self.window.output.toPlainText(), namespace)
        self.assertEqual(namespace["f"](4), 10)


if __name__ == "__main__":
    unittest.main()
