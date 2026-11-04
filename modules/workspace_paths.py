"""Canonical paths for containment checks, including Windows extended paths."""
import os
from pathlib import Path
import re


def resolved_path(value):
    # Resolve links BEFORE normalizing spelling; never use lexical prefix tests
    # to decide whether a path is inside a workspace.
    path = Path(value).expanduser().resolve()
    text = str(path)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif re.match(r"^\\\\\?\\[A-Za-z]:\\", text):
            text = text[4:]
    return Path(text)
