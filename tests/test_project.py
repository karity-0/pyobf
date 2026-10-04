import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyobf.project import build_project, ProjectBuildError, ProjectCancelled
from pyobf.source import SourceDocument


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.parent = Path(self.directory.name)
        self.source = self.parent / 'app'
        self.source.mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, text):
        file = self.source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')
        return file

    def execute(self, output, code):
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=output, capture_output=True,
                                text=True, encoding='utf-8', timeout=10,
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_packages_imports_keyword_api_resources_and_integrity(self):
        self.write('pkg/__init__.py', 'from .helper import exported_function, public_value\n')
        self.write('pkg/helper.py', '''public_value = 24
def exported_function(keyword_argument=3):
    @protect_start(cff, bcf, proxy, morph, integrity)
    long_local_variable = keyword_argument + public_value
    for index in range(2):
        long_local_variable += ord("A") - 64
    text = @{"hello"}
    return text, long_local_variable
    @protect_end
''')
        self.write('main.py', 'from pkg import exported_function, public_value\nprint(exported_function(keyword_argument=4), public_value)')
        resource = self.source / 'data.bin'
        resource.write_bytes(b'\x00\xff\x01')
        (self.source / 'empty').mkdir()
        snapshots = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        result = build_project(self.source, self.parent)
        self.assertEqual((result.scripts, result.resources), (3, 1))
        self.assertEqual(result.output.name, 'app.obf')
        self.assertEqual((result.output / 'data.bin').read_bytes(), resource.read_bytes())
        self.assertTrue((result.output / 'empty').is_dir())
        self.assertEqual(self.execute(result.output, 'import main'), "('hello', 30) 24\n")
        transformed = (result.output / 'pkg/helper.py').read_text(encoding='utf-8')
        self.assertIn('exported_function', transformed)
        self.assertIn('public_value', transformed)
        self.assertNotIn('long_local_variable', transformed)
        self.assertNotIn('@protect_', transformed)
        for path, original in snapshots.items():
            self.assertEqual(path.read_bytes(), original)

    def test_failures_are_aggregated_and_no_partial_output_is_published(self):
        self.write('good.py', 'x = 24')
        self.write('bad.py', 'x = (')
        self.write('nested/bad.py', '@protect_start(unknown)\nx=1\n@protect_end')
        with self.assertRaises(ProjectBuildError) as raised:
            build_project(self.source, self.parent)
        self.assertEqual({Path(name).as_posix() for name, _ in raised.exception.errors}, {'bad.py', 'nested/bad.py'})
        self.assertEqual(list(self.parent.iterdir()), [self.source])

    def test_repeat_builds_never_overwrite_previous_outputs(self):
        self.write('main.py', 'value = 1')
        first = build_project(self.source, self.parent)
        (first.output / 'main.py').write_text('keep this', encoding='utf-8')
        second = build_project(self.source, self.parent)
        self.assertEqual(second.output.name, 'app.obf-2')
        self.assertEqual((first.output / 'main.py').read_text(encoding='utf-8'), 'keep this')

    def test_venvs_caches_vcs_and_bytecode_are_excluded(self):
        self.write('main.py', 'value = 1')
        for name in ('.venv/bad.py', 'venv/bad.py', '.git/bad.py', '__pycache__/bad.py', 'custom_env/pyvenv.cfg', 'custom_env/bad.py'):
            self.write(name, 'not valid Python!')
        self.write('cached.pyc', 'bytecode')
        result = build_project(self.source, self.parent)
        self.assertEqual(sorted(path.name for path in result.output.iterdir()), ['main.py'])

    def test_cancellation_removes_staging_and_keeps_original(self):
        original = self.write('main.py', 'value = 24')
        cancelled = False
        def progress(*args):
            nonlocal cancelled
            cancelled = True
        with self.assertRaises(ProjectCancelled):
            build_project(self.source, self.parent, progress=progress, cancelled=lambda: cancelled)
        self.assertEqual(list(self.parent.iterdir()), [self.source])
        self.assertEqual(original.read_text(encoding='utf-8'), 'value = 24')

    def test_resource_copy_error_publishes_nothing(self):
        self.write('main.py', 'value = 1')
        self.write('asset.txt', 'data')
        with patch('pyobf.project.shutil.copy2', side_effect=OSError('copy failed')):
            with self.assertRaises(ProjectBuildError) as raised:
                build_project(self.source, self.parent)
        self.assertIn('asset.txt', str(raised.exception))
        self.assertEqual(list(self.parent.iterdir()), [self.source])

    def test_destination_validation_and_empty_projects(self):
        self.write('main.py', 'value = 1')
        nested = self.source / 'nested'
        nested.mkdir()
        for destination in (self.source, nested):
            with self.assertRaisesRegex(ValueError, 'output_inside_project'):
                build_project(self.source, destination)
        (self.source / 'main.py').unlink()
        with self.assertRaisesRegex(ValueError, 'no_python_files'):
            build_project(self.source, self.parent)

    def test_buffer_snapshot_encodings_and_no_user_code_execution(self):
        original = self.write('main.py', 'raise RuntimeError("must not execute")')
        bom = self.source / 'other.pyw'
        bom.write_bytes(b'\xef\xbb\xbfvalue = 24\r\n')
        korean = self.source / 'legacy.py'
        korean.write_bytes('# coding: cp949\r\nmessage = "안녕"\r\n'.encode('cp949'))
        snapshot = SourceDocument('print("editor buffer")', str(original))
        result = build_project(self.source, self.parent, documents=(snapshot,))
        self.assertEqual(self.execute(result.output, 'import main'), 'editor buffer\n')
        self.assertTrue((result.output / 'other.pyw').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn('\r\n', SourceDocument.read(result.output / 'legacy.py').text)
        self.assertEqual(self.execute(result.output, 'import legacy; print(legacy.message)'), '안녕\n')
        self.assertIn('must not execute', original.read_text(encoding='utf-8'))

    def test_external_symlinks_are_not_followed(self):
        self.write('main.py', 'value = 1')
        outside = self.parent / 'outside'
        outside.mkdir()
        (outside / 'bad.py').write_text('not valid Python!', encoding='utf-8')
        try:
            os.symlink(outside, self.source / 'linked', target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        result = build_project(self.source, self.parent)
        self.assertFalse((result.output / 'linked').exists())


if __name__ == '__main__':
    unittest.main()
