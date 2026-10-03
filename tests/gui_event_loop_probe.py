"""Run the GUI's real event loop in a child process with an external timeout."""

import faulthandler
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QThread, QTimer

from pyobf.gui.app import create_application
from pyobf.gui.window import MainWindow
from pyobf.gui.settings import SettingsStore
from pyobf.pipeline import Pipeline


def main():
    app = create_application()
    settings_directory = tempfile.TemporaryDirectory()
    window = MainWindow(SettingsStore(Path(settings_directory.name) / "preferences.json"))
    plain = 'print("hello world")\nx= "hi"'
    protected = 'def f(n):\n    @protect_start(cff, junk)\n    x = n + 1\n    for i in range(4):\n        if i % 2:\n            continue\n        x += i\n    x *= 2\n    @protect_end\n    return x\n'
    jobs = [plain, 'x = @{"hello world"}', protected, "x = ("] * 10
    index = 0
    pending = False
    running_ticks = 0
    failures = []
    diagnostics = []
    delayed = False
    execution_stage = 0
    original_run = Pipeline.run
    window._show_error = diagnostics.append

    def delayed_run(pipeline, source):
        nonlocal delayed
        assert QThread.currentThread() != app.thread(), "Analysis ran on the GUI thread"
        if not delayed:
            delayed = True
            time.sleep(0.15)
        return original_run(pipeline, source)

    def tick():
        nonlocal index, pending, running_ticks, execution_stage
        try:
            if index == len(jobs):
                if window._thread is not None or window.runner.running:
                    running_ticks += 1
                    if execution_stage == 3 and window.runner.running:
                        window.stop_button.click()
                    return
                if execution_stage == 0:
                    for theme in ("white", "dark", "crystal", "ocean"):
                        window.set_preferences(theme, "en")
                    window.input.setPlainText('print(@{"native execution"})')
                    execution_stage = 1
                    window.input_play_button.click()
                elif execution_stage == 1:
                    assert "native execution" in window.console.toPlainText()
                    assert "exit 0" in window.status.text()
                    execution_stage = 2
                    window.output_play_button.click()
                elif execution_stage == 2:
                    assert "native execution" in window.console.toPlainText()
                    window.input.setPlainText("while True: pass")
                    execution_stage = 3
                    window.input_play_button.click()
                else:
                    assert window.status.text() == "Stopped"
                    assert window.run_button.isEnabled()
                    app.quit()
                return
            if pending and window._thread is not None:
                running_ticks += 1
                return
            if pending:
                assert not window.input.isReadOnly()
                assert window.run_button.isEnabled()
                assert window.open_button.isEnabled()
                source = jobs[index]
                if source == plain:
                    assert window.output.toPlainText() == plain
                    assert "원본 유지" in window.status.text()
                elif "@{" in source:
                    assert window._result is not None
                    assert window._result.pass_records[0].replacements == 1
                    assert "StripInfoPass" in window._result.applied_passes
                    assert "hello world" not in window.output.toPlainText()
                elif source == protected:
                    assert window._result is not None
                    assert window._result.applied_passes == ("ProtectionPass", "StripInfoPass")
                    assert "@protect_" not in window.output.toPlainText()
                    namespace = {}
                    exec(window.output.toPlainText(), namespace)
                    assert namespace["f"](4) == 14
                else:
                    assert window._result is None
                    assert not window.save_button.isEnabled()
                    assert not window.copy_button.isEnabled()
                index += 1
                pending = False
                if index == len(jobs):
                    assert len(diagnostics) == 10
                    assert running_ticks >= 3, "GUI timer stopped while analysis was running"
                    return
            window.input.setPlainText(jobs[index])
            pending = True
            window.run_button.click()
        except Exception as error:
            failures.append(str(error))
            app.exit(1)

    def timeout():
        failures.append("Qt event loop did not finish all jobs")
        app.exit(1)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(20)
    QTimer.singleShot(10000, timeout)
    faulthandler.dump_traceback_later(8)
    with patch("pyobf.gui.window.Pipeline.run", delayed_run):
        exit_code = app.exec()
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps({"completed": index, "executions": execution_stage, "running_ticks": running_ticks, "failures": failures}), flush=True)
    if window._thread is None:
        window.close()
    settings_directory.cleanup()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
