"""Build a source project in isolation, retaining module paths and resources."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .pipeline import Pipeline
from .source import SourceDocument


IGNORED_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', '__pycache__',
                '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', 'node_modules'}
PYTHON_SUFFIXES = {'.py', '.pyw', '.pyobf'}


def _linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) &
                                    getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


class ProjectCancelled(Exception):
    pass


class ProjectBuildError(Exception):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__('\n'.join(f'{path}: {message}' for path, message in self.errors))


@dataclass(frozen=True)
class ProjectResult:
    output: Path
    scripts: int
    resources: int


def validate_destination(source, parent):
    source, parent = Path(source).resolve(), Path(parent).resolve()
    if not source.is_dir() or not parent.is_dir():
        raise ValueError('missing_directory')
    if parent == source or source in parent.parents:
        raise ValueError('output_inside_project')
    return source, parent


def build_project(source, parent, *, progress=None, cancelled=None, documents=()):
    source, parent = validate_destination(source, parent)
    documents = {Path(document.filename).resolve(): document for document in documents}

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise ProjectCancelled()

    files, directories = [], []
    def walk_error(error):
        raise error
    for root, children, names in os.walk(source, followlinks=False, onerror=walk_error):
        check_cancelled()
        root = Path(root)
        children[:] = sorted(name for name in children if name not in IGNORED_DIRS and not _linked(root / name))
        # venvs can have arbitrary names; don't copy their interpreter packages.
        children[:] = [name for name in children if not (root / name / 'pyvenv.cfg').is_file()]
        directories.append(root.relative_to(source))
        for name in sorted(names):
            file = root / name
            if not _linked(file) and file.is_file() and file.suffix.lower() not in {'.pyc', '.pyo'}:
                files.append(file.relative_to(source))
    scripts = sum(path.suffix.lower() in PYTHON_SUFFIXES for path in files)
    if not scripts:
        raise ValueError('no_python_files')
    errors = []
    # Publish only after every script and resource succeeded. Existing projects
    # and outputs never get overwritten; cancellation removes staging files.
    with tempfile.TemporaryDirectory(prefix='.pyobf-', dir=parent) as temporary:
        staging = Path(temporary) / 'project'
        for relative in directories:
            (staging / relative).mkdir(parents=True, exist_ok=True)
        for index, relative in enumerate(files, 1):
            check_cancelled()
            if progress is not None:
                progress(index, len(files), str(relative))
            original, output = source / relative, staging / relative
            try:
                if relative.suffix.lower() in PYTHON_SUFFIXES:
                    document = documents.get(original) or SourceDocument.read(original)
                    result = Pipeline(preserve_module_names=True).run(document)
                    check_cancelled()
                    output.write_bytes(result.source.to_bytes())
                    shutil.copymode(original, output)
                else:
                    shutil.copy2(original, output)
            except ProjectCancelled:
                raise
            except Exception as error:
                errors.append((str(relative), f'{type(error).__name__}: {error}'))
        check_cancelled()
        if errors:
            raise ProjectBuildError(errors)
        number = 1
        while True:
            output = parent / (source.name + '.obf' + (f'-{number}' if number > 1 else ''))
            if output.exists():
                number += 1
                continue
            try:
                staging.rename(output)
                break
            except FileExistsError:
                number += 1
        return ProjectResult(output, scripts, len(files) - scripts)
