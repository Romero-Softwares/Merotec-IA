import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def backup_corrupt_json(path):
    path = Path(path)
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}.bak")
    try:
        shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def load_json_file(path, default, expected_type=None, *, backup_invalid=True):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        if backup_invalid:
            backup_corrupt_json(path)
        return default
    except OSError:
        return default

    if expected_type is not None and not isinstance(loaded, expected_type):
        return default
    return loaded


def atomic_write_json(path, payload, *, indent=2, ensure_ascii=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_name = file.name
            json.dump(payload, file, indent=indent, ensure_ascii=ensure_ascii)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
        return True
    except (OSError, TypeError, ValueError):
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        return False
