"""Transporte seguro de codigo e validacao sintatica inicial."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SOURCE_SUFFIXES = {
    ".py",
    ".pyw",
    ".json",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".dart",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".ps1",
    ".sh",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyw": "python",
    ".json": "json",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".ps1": "powershell",
    ".sh": "shell",
}

VOID_HTML_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def source_language(path):
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "text")


def unwrap_transport_code(content):
    """Remove somente as cercas externas, preservando cada espaco do codigo."""
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip("\n")
    lines = text.split("\n")
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _issue(path, language, kind, message, line=0, excerpt=""):
    return {
        "path": str(path),
        "language": language,
        "kind": kind,
        "message": message,
        "line": int(line or 0),
        "excerpt": excerpt,
    }


def _balanced_delimiters(path, language, text):
    pairs = {"(": ")", "[": "]", "{": "}"}
    reverse = {value: key for key, value in pairs.items()}
    stack = []
    quote = ""
    escaped = False
    line = 1

    for char in text:
        if char == "\n":
            line += 1
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            stack.append((char, line))
        elif char in reverse:
            if not stack:
                return _issue(path, language, "DelimiterError", f"Delimitador inesperado: {char}", line)
            opened, opened_line = stack.pop()
            if opened != reverse[char]:
                return _issue(
                    path,
                    language,
                    "DelimiterError",
                    f"Delimitador incompativel: {opened} aberto na linha {opened_line}.",
                    line,
                )

    if quote:
        return _issue(path, language, "StringError", "String sem fechamento.", line)
    if stack:
        opened, opened_line = stack[-1]
        return _issue(path, language, "DelimiterError", f"Delimitador sem fechamento: {opened}.", opened_line)
    return None


def validate_source_text(path, content):
    """Retorna None quando o texto pode ser gravado; caso contrario um diagnostico."""
    path = Path(path)
    language = source_language(path)
    text = str(content or "")

    if path.suffix.lower() not in SOURCE_SUFFIXES:
        return None

    if language == "python":
        for number, line in enumerate(text.splitlines(), start=1):
            leading = line[: len(line) - len(line.lstrip(" \t"))]
            if "\t" in leading:
                return _issue(
                    path,
                    language,
                    "TabIndentationRejected",
                    "Tabs nao sao permitidos em codigo Python. Use quatro espacos por nivel.",
                    number,
                    f"{number:>4}: {line}",
                )
        try:
            ast.parse(text, filename=str(path))
            return None
        except (SyntaxError, IndentationError, TabError) as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            lines = text.splitlines()
            start = max(1, line - 3)
            end = min(len(lines), max(start, line + 3))
            excerpt = "\n".join(
                f"{index:>4}: {lines[index - 1]}"
                for index in range(start, end + 1)
            )
            return _issue(
                path,
                language,
                exc.__class__.__name__,
                str(getattr(exc, "msg", "") or exc),
                line,
                excerpt,
            )

    if language == "json":
        try:
            json.loads(text)
            return None
        except json.JSONDecodeError as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            lines = text.splitlines()
            excerpt = lines[line - 1] if 0 < line <= len(lines) else ""
            return _issue(path, language, "JSONDecodeError", exc.msg, line, excerpt)

    if language in {"javascript", "typescript", "dart", "css"}:
        return _balanced_delimiters(path, language, text)

    if language == "html":
        open_tags = []
        for match in re.finditer(r"</?([A-Za-z][\w:-]*)\b[^>]*>", text):
            token = match.group(0)
            tag = match.group(1).lower()
            if token.startswith("</"):
                if open_tags and open_tags[-1] == tag:
                    open_tags.pop()
                elif tag not in VOID_HTML_TAGS:
                    return _issue(path, language, "HTMLStructureError", f"Fechamento HTML incompativel: </{tag}>.")
            elif not token.endswith("/>") and tag not in VOID_HTML_TAGS:
                open_tags.append(tag)
        if open_tags:
            return _issue(path, language, "HTMLStructureError", f"Tag HTML sem fechamento: <{open_tags[-1]}>.")
    return None


def validate_source(path, content):
    """Alias de compatibilidade para validadores antigos."""
    return validate_source_text(path, content)


def fenced_transport_instruction(path, issue=None):
    """Instrucao de transporte para chats web sem forcar reescrita de arquivo grande."""
    language = source_language(path)
    issue_text = ""
    if issue:
        issue_text = (
            f"\nA ultima tentativa foi recusada: {issue.get('kind')}: "
            f"{issue.get('message')} (linha {issue.get('line') or 'nao informada'}).\n"
        )
    return f"""
PROTOCOLO INCREMENTAL V9:
Para editar `{Path(path).as_posix()}`, nao e obrigatorio reescrever o arquivo inteiro.

Mudanca local preferida:
[REPLACE: caminho/arquivo]
[OLD]
```{language}
trecho atual exato
```
[/OLD]
[NEW]
```{language}
trecho novo
```
[/NEW]
[/REPLACE]

Patch incremental tambem e aceito:
[PATCH]
*** Begin Patch
*** Update File: caminho/arquivo
@@
-trecho antigo
+trecho novo
*** End Patch
[/PATCH]

Use [WRITE] somente para criar arquivo ou reescrever o conteudo completo de proposito.
Quando faltar contexto em arquivo grande, solicite [READ: caminho | linhas inicio-fim] ou [SEARCH_TEXT: padrao | caminho].
Nunca escreva codigo-fonte multiline fora de uma cerca Markdown.
Python exige quatro espacos por nivel, nunca tab.
{issue_text}
""".strip()
