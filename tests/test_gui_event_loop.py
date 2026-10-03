import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class GuiEventLoopTests(unittest.TestCase):
    def run_probe(self, platform):
        repository = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = platform
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONPATH"] = str(repository) + os.pathsep + environment.get("PYTHONPATH", "")
        # Separate processes bound deadlocks that a Qt timer cannot interrupt.
        result = subprocess.run(
            [sys.executable, "-u", str(Path(__file__).with_name("gui_event_loop_probe.py"))],
            cwd=repository, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(report["completed"], 40)
        self.assertEqual(report["failures"], [])

    def test_real_event_loop_remains_responsive(self):
        self.run_probe("offscreen")

    @unittest.skipUnless(os.name == "nt", "Windows Qt platform regression")
    def test_native_windows_event_loop_remains_responsive(self):
        self.run_probe("windows")
