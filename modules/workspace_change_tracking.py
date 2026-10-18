"""Detecção leve de mudanças diretas feitas por ferramentas do agente."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from modules.app_constants import is_ignored_dir_name


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    files: dict[str, tuple[int, int]]
    truncated: bool = False


def capture_workspace_snapshot(workspace, max_files=4000) -> WorkspaceSnapshot:
    """Captura tamanho e mtime de arquivos relevantes, sem seguir diretórios ignorados."""
    root = Path(workspace).resolve()
    files = {}
    truncated = False
    try:
        for current, directory_names, file_names in os.walk(root):
            current_path = Path(current)
            directory_names[:] = [
                name for name in directory_names if not is_ignored_dir_name(name)
            ]
            for name in file_names:
                path = current_path / name
                try:
                    relative = path.relative_to(root).as_posix()
                    stat = path.stat()
                except (OSError, ValueError):
                    continue
                files[relative] = (int(stat.st_size), int(stat.st_mtime_ns))
                if len(files) >= max(1, int(max_files)):
                    truncated = True
                    break
            if truncated:
                break
    except OSError:
        pass
    return WorkspaceSnapshot(root=root, files=files, truncated=truncated)


def changed_workspace_paths(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[Path]:
    """Retorna criações, modificações e remoções observadas entre dois snapshots."""
    if before.root != after.root:
        return []
    changed = [
        before.root / relative
        for relative in sorted(set(before.files) | set(after.files))
        if before.files.get(relative) != after.files.get(relative)
    ]
    return changed

