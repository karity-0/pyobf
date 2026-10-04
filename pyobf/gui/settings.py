import json
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .themes import THEMES
from .designs import DESIGNS


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "preferences.json"
        self.data = {"theme": "white", "language": "ko", "design": "studio", "recent_files": [], "recent_projects": []}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                if saved.get("theme") in THEMES:
                    self.data["theme"] = saved["theme"]
                if saved.get("language") in ("en", "ko"):
                    self.data["language"] = saved["language"]
                if saved.get("design") in DESIGNS:
                    self.data["design"] = saved["design"]
                for key in ("recent_files", "recent_projects"):
                    if isinstance(saved.get(key), list):
                        self.data[key] = [value for value in saved[key] if isinstance(value, str)][:8]
        except (OSError, ValueError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def preferences(self, theme, language, design=None):
        design = self.data["design"] if design is None else design
        if theme not in THEMES or language not in ("en", "ko") or design not in DESIGNS:
            raise ValueError("Invalid preferences")
        self.data.update(theme=theme, language=language, design=design)
        self.save()

    def remember(self, kind, path):
        key = "recent_files" if kind == "file" else "recent_projects"
        path = str(Path(path).resolve())
        self.data[key] = [path] + [old for old in self.data[key] if os.path.normcase(old) != os.path.normcase(path)]
        self.data[key] = self.data[key][:8]
        self.save()
