import threading
from pathlib import Path
from modules.app_constants import DEFAULT_WORKSPACE

class ProjectLoader:
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
                        'type': 'python' if (project/'main.py').exists() else 'web'
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
