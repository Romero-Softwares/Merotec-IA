import threading
from pathlib import Path
from modules.app_constants import DEFAULT_WORKSPACE

class ProjectLoader:
    @staticmethod
    def detect_project_type(project):
        if (project / "pubspec.yaml").exists():
            try:
                pubspec = (project / "pubspec.yaml").read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                pubspec = ""
            return "flutter" if "flutter:" in pubspec or "sdk: flutter" in pubspec else "dart"
        if (project / "requirements.txt").exists():
            try:
                requirements = (project / "requirements.txt").read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                requirements = ""
            if "flet" in requirements:
                return "flet"
        if (project / "main.py").exists() or (project / "app.py").exists():
            return "python"
        if (project / "package.json").exists():
            return "node"
        return "web"

    @staticmethod
    def preload_projects():
        """Preload project metadata in background."""
        projects_dir = DEFAULT_WORKSPACE
        if projects_dir.exists():
            for project in projects_dir.iterdir():
                if project.is_dir():
                    # Load basic metadata without full parsing
                    metadata = {
                        'name': project.name,
                        'path': str(project),
                        'type': ProjectLoader.detect_project_type(project)
                    }
                    ProjectCache.add(project.name, metadata)

class ProjectCache:
    _cache = {}
    
    @classmethod
    def add(cls, name, metadata):
        cls._cache[name] = metadata
        
    @classmethod
    def get(cls, name):
        return cls._cache.get(name)
