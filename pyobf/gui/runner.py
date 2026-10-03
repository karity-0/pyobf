import codecs
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal


LAUNCHER = """import sys, pathlib, types, linecache, tokenize, io
snapshot, filename = sys.argv[1:3]
data = pathlib.Path(snapshot).read_bytes()
header = data.replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n')
encoding, _ = tokenize.detect_encoding(io.BytesIO(header).readline)
linecache.cache[filename] = (len(data), None, data.decode(encoding).splitlines(True), filename)
sys.argv = [filename]
sys.path[0] = str(pathlib.Path(filename).parent)
module = types.ModuleType('__main__')
module.__dict__.update(__file__=filename, __package__=None, __spec__=None, __cached__=None)
sys.modules['__main__'] = module
exec(compile(data, filename, 'exec'), module.__dict__)
"""


class PythonRunner(QObject):
    output = Signal(str, bool)
    started = Signal()
    finished = Signal(int, bool)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.started.connect(self.started)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.error)
        self._temporary = None
        self._stopped = False
        self._decoders = []

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, source, working_directory, project=None):
        if self.running:
            return False
        self._temporary = tempfile.TemporaryDirectory(prefix="pyobf-run-")
        snapshot = Path(self._temporary.name) / "script.py"
        try:
            snapshot.write_bytes(source.to_bytes())
        except Exception:
            self.cleanup()
            raise
        self._stopped = False
        self._decoders = [codecs.getincrementaldecoder("utf-8")("replace") for _ in range(2)]
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        paths = [str(working_directory)]
        if project and str(project) not in paths:
            paths.append(str(project))
        if environment.contains("PYTHONPATH"):
            paths.append(environment.value("PYTHONPATH"))
        environment.insert("PYTHONPATH", os.pathsep.join(paths))
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(working_directory))
        self.process.setProgram(sys.executable)
        filename = str(snapshot) if source.filename.startswith("<") else source.filename
        self.process.setArguments(["-u", "-c", LAUNCHER, str(snapshot), filename])
        self.process.start()
        return True

    def read_stdout(self):
        if self._decoders:
            text = self._decoders[0].decode(bytes(self.process.readAllStandardOutput()))
            if text:
                self.output.emit(text, False)

    def read_stderr(self):
        if self._decoders:
            text = self._decoders[1].decode(bytes(self.process.readAllStandardError()))
            if text:
                self.output.emit(text, True)

    def complete(self, code, status):
        self.read_stdout()
        self.read_stderr()
        for index, decoder in enumerate(self._decoders):
            final = decoder.decode(b"", final=True)
            if final:
                self.output.emit(final, bool(index))
        stopped = self._stopped
        self.cleanup()
        self.finished.emit(code, stopped)

    def error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            message = self.process.errorString()
            self.cleanup()
            self.failed.emit(message)

    def send(self, text):
        if self.running:
            self.process.write((text + "\n").encode("utf-8"))

    def stop(self):
        if self.running:
            self._stopped = True
            self.process.kill()

    def cleanup(self):
        self._decoders = []
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
