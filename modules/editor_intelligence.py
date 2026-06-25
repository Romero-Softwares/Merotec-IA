"""Fast local editor intelligence that works without an AI provider or LSP."""

from __future__ import annotations

import ast
import builtins
import keyword
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EditorSymbol:
    name: str
    kind: str
    line: int
    detail: str = ""


_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")
_VOCABULARY = {
    ".py": ("def", "class", "if", "for", "while", "try", "with", "import", "from", "return", "async", "await"),
    ".js": ("function", "class", "const", "let", "if", "for", "try", "import", "export", "async", "await", "return"),
    ".jsx": ("function", "class", "const", "useState", "useEffect", "return", "export", "import"),
    ".ts": ("function", "class", "interface", "type", "const", "let", "import", "export", "async", "await", "return"),
    ".tsx": ("function", "class", "interface", "type", "const", "useState", "useEffect", "return", "export", "import"),
    ".cs": ("class", "interface", "namespace", "public", "private", "protected", "static", "async", "await", "return"),
    ".java": ("class", "interface", "public", "private", "protected", "static", "final", "return", "new"),
    ".html": ("div", "section", "main", "button", "input", "script", "style", "class", "id"),
    ".css": ("display", "position", "color", "background", "margin", "padding", "grid", "flex", "font-size"),
    ".json": ("true", "false", "null"),
}


def word_prefix(text: str, cursor_offset: int | None = None) -> str:
    before = text if cursor_offset is None else text[: max(0, cursor_offset)]
    match = re.search(r"[A-Za-z_$][\w$]*$", before)
    return match.group(0) if match else ""


def completion_items(text: str, file_path: str | None = None, cursor_offset: int | None = None, limit: int = 80) -> list[str]:
    """Return ranked completion labels from the buffer and language vocabulary."""
    prefix = word_prefix(text, cursor_offset)
    lowered = prefix.lower()
    suffix = Path(file_path).suffix.lower() if file_path else ""
    words = set(_WORD_RE.findall(text))
    words.update(_VOCABULARY.get(suffix, ()))
    if suffix == ".py" or not suffix:
        words.update(keyword.kwlist)
        words.update(name for name in dir(builtins) if not name.startswith("_"))
    candidates = [item for item in words if item != prefix and (not prefix or item.lower().startswith(lowered))]
    candidates.sort(key=lambda item: (len(item), item.lower(), item))
    return candidates[: max(1, limit)]


def _python_symbols(text: str) -> list[EditorSymbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    symbols = []
    class_ranges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
            symbols.append(EditorSymbol(node.name, "class", node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(arg.arg for arg in node.args.args)
            symbols.append(EditorSymbol(node.name, "function", node.lineno, f"({args})"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            name = node.module if isinstance(node, ast.ImportFrom) else ", ".join(alias.name for alias in node.names)
            symbols.append(EditorSymbol(name or "import", "import", node.lineno))
    result = []
    for symbol in symbols:
        if symbol.kind == "function":
            line = lines[symbol.line - 1] if 0 < symbol.line <= len(lines) else ""
            indent = len(line) - len(line.lstrip())
            if indent and any(start < symbol.line <= end for start, end in class_ranges):
                symbol = EditorSymbol(symbol.name, "method", symbol.line, symbol.detail)
        result.append(symbol)
    return sorted(result, key=lambda item: (item.line, item.name.lower()))


def extract_symbols(text: str, file_path: str | None = None) -> list[EditorSymbol]:
    """Extract navigable symbols for common development file types."""
    suffix = Path(file_path).suffix.lower() if file_path else ""
    if suffix == ".py" or (not suffix and re.search(r"^\s*(def|class)\s+", text, re.MULTILINE)):
        return _python_symbols(text)
    patterns = (
        ("class", r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|static\s+|abstract\s+)*class\s+([A-Za-z_$][\w$]*)"),
        ("interface", r"^\s*(?:export\s+)?(?:public\s+)?interface\s+([A-Za-z_$][\w$]*)"),
        ("function", r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"),
        ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>"),
        ("method", r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+|override\s+|virtual\s+)*[\w<>,\[\]?]+\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*[{:]"),
    )
    symbols = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for kind, pattern in patterns:
            match = re.search(pattern, line)
            if match:
                detail = f"({match.group(2).strip()})" if match.lastindex and match.lastindex > 1 else ""
                symbols.append(EditorSymbol(match.group(1), kind, line_no, detail))
                break
        if suffix in {".md", ".markdown"}:
            heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
            if heading:
                symbols.append(EditorSymbol(heading.group(2), "heading", line_no, heading.group(1)))
        elif suffix in {".html", ".htm"}:
            for match in re.finditer(r"\bid=[\"']([^\"']+)[\"']", line):
                symbols.append(EditorSymbol(match.group(1), "id", line_no, "#"))
        elif suffix in {".css", ".scss", ".sass"}:
            selector = re.match(r"^\s*([^@][^{]+)\s*\{", line)
            if selector:
                symbols.append(EditorSymbol(selector.group(1).strip(), "selector", line_no))
    return sorted(symbols, key=lambda item: (item.line, item.name.lower()))
