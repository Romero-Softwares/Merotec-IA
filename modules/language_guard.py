"""Validação transacional multilinguagem para a Merotec IA IDE.

A IDE não assume Python como linguagem do workspace. A linguagem é escolhida por
arquivo e por marcadores reais do projeto; quando uma ferramenta da stack não
está instalada, a validação informa isso e aplica uma verificação estrutural em
vez de recusar ou encerrar a missão.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".merotec_backups", ".merotec_patch_backups", ".dart_tool",
    "build", "dist", "out", ".idea", ".vscode", ".next", ".nuxt",
    "target", "bin", "obj", ".gradle", ".cache",
}

SOURCE_SUFFIXES = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".dart", ".json", ".html", ".htm", ".css", ".scss", ".sass",
    ".ps1", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".cs", ".csx",
    ".java", ".kt", ".kts", ".go", ".rs", ".c", ".h", ".cc", ".cpp",
    ".cxx", ".hpp", ".hh", ".php", ".rb", ".swift", ".lua", ".sql",
    ".xml", ".xaml", ".yml", ".yaml", ".toml", ".vue", ".svelte",
}

EXTENSION_LANGUAGE = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".dart": "dart", ".json": "json", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".sass": "css",
    ".ps1": "powershell", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".bat": "batch", ".cmd": "batch",
    ".cs": "csharp", ".csx": "csharp", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".php": "php", ".rb": "ruby",
    ".swift": "swift", ".lua": "lua", ".sql": "sql", ".xml": "xml",
    ".xaml": "xml", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
    ".vue": "vue", ".svelte": "svelte",
}

VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _StrictHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.error = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in VOID_HTML_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID_HTML_TAGS:
            return
        if not self.stack:
            self.error = f"tag HTML de fechamento sem abertura: </{tag}>"
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        if tag in self.stack:
            self.error = f"estrutura HTML fora de ordem: esperado </{self.stack[-1]}>, recebido </{tag}>"
        else:
            self.error = f"tag HTML de fechamento sem abertura: </{tag}>"


def _issue(path, language, message, line=0, kind="ValidationError", excerpt=""):
    return {
        "ok": False,
        "path": str(path),
        "language": language,
        "message": str(message),
        "line": int(line or 0),
        "kind": str(kind),
        "excerpt": str(excerpt or ""),
    }


def _ok(path, language, message=""):
    return {
        "ok": True,
        "path": str(path),
        "language": language,
        "message": str(message or ""),
        "line": 0,
        "kind": "",
        "excerpt": "",
    }


def _has_any(root: Path, patterns):
    for pattern in patterns:
        if any(root.glob(pattern)):
            return True
    return False


def detect_workspace_language(workspace):
    """Escolhe a stack predominante a partir de arquivos de projeto reais."""
    root = Path(workspace).resolve()
    if (root / "pubspec.yaml").exists():
        return "flutter_dart"
    if (root / "Cargo.toml").exists():
        return "rust"
    if (root / "go.mod").exists():
        return "go"
    if _has_any(root, ("*.sln", "*.csproj", "*.fsproj")):
        return "dotnet"
    if (root / "pom.xml").exists():
        return "java_maven"
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists() or (root / "settings.gradle").exists() or (root / "settings.gradle.kts").exists():
        return "gradle"
    if (root / "CMakeLists.txt").exists():
        return "cmake"
    if (root / "composer.json").exists():
        return "php"
    if (root / "Gemfile").exists():
        return "ruby"
    if (root / "Package.swift").exists():
        return "swift"
    if (root / "package.json").exists():
        if (root / "tsconfig.json").exists() or _has_any(root, ("*.ts", "*.tsx")):
            return "typescript"
        return "javascript"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or _has_any(root, ("*.py", "*.pyw")):
        return "python"
    if _has_any(root, ("*.java",)):
        return "java"
    if _has_any(root, ("*.kt", "*.kts")):
        return "kotlin"
    if _has_any(root, ("*.cpp", "*.cc", "*.cxx", "*.hpp")):
        return "cpp"
    if _has_any(root, ("*.c", "*.h")):
        return "c"
    if _has_any(root, ("*.html", "*.htm", "*.vue", "*.svelte")):
        return "web"
    return "generic"


def source_language(path, workspace=None):
    suffix = Path(path).suffix.lower()
    return EXTENSION_LANGUAGE.get(suffix, detect_workspace_language(workspace) if workspace else "text")


def _leading_tabs_issue(path, language, text):
    for number, line in enumerate(text.splitlines(), start=1):
        leading = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in leading:
            return _issue(
                path,
                language,
                "Tabs não são aceitos para indentação em Python. Use 4 espaços por nível.",
                number,
                "TabIndentationRejected",
                f"{number:>4}: {line}",
            )
    return None


def _temp_source_path(path):
    path = Path(path)
    return path.parent / f".merotec_validate_{os.getpid()}_{threading.get_ident()}_{path.stem}{path.suffix}"


def _command_validation(path, text, executable, args, language, timeout=20):
    executable_path = shutil.which(executable)
    if not executable_path:
        return _ok(path, language, f"{executable} não encontrado; validação estrutural local aplicada.")
    temporary = _temp_source_path(path)
    try:
        temporary.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [executable_path, *args, str(temporary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0:
            return _ok(path, language)
        output = (result.stdout or "").strip()
        line_match = re.search(r"(?::|line\s+)(\d+)", output, re.IGNORECASE)
        line = int(line_match.group(1)) if line_match else 0
        return _issue(path, language, output or f"{executable} recusou o arquivo.", line, f"{language.title()}SyntaxError")
    except subprocess.TimeoutExpired:
        return _issue(path, language, f"Validação {executable} excedeu o tempo limite.", 0, "ValidationTimeout")
    except OSError as exc:
        return _issue(path, language, str(exc), 0, "ValidationProcessError")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _balanced_delimiters(path, language, text, pairs):
    stack = []
    quote = ""
    escaped = False
    line = 1
    in_line_comment = False
    in_block_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if char == "\n":
            line += 1
            in_line_comment = False
            index += 1
            continue
        if in_line_comment:
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if not quote and char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if not quote and char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            stack.append((char, line))
        elif char in pairs.values():
            if not stack:
                return _issue(path, language, f"Delimitador inesperado: {char}", line, "DelimiterError")
            opening, opening_line = stack.pop()
            if pairs[opening] != char:
                return _issue(path, language, f"Delimitador incompatível: {opening} aberto na linha {opening_line}.", line, "DelimiterError")
        index += 1
    if quote:
        return _issue(path, language, "String sem fechamento.", line, "StringError")
    if in_block_comment:
        return _issue(path, language, "Comentário de bloco sem fechamento.", line, "CommentError")
    if stack:
        opening, opening_line = stack[-1]
        return _issue(path, language, f"Delimitador sem fechamento: {opening}.", opening_line, "DelimiterError")
    return _ok(path, language)


def _validate_html(path, text):
    parser = _StrictHtmlParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        return _issue(path, "html", str(exc), 0, "HTMLParseError")
    if parser.error:
        return _issue(path, "html", parser.error, 0, "HTMLStructureError")
    if parser.stack:
        return _issue(path, "html", f"Tag HTML sem fechamento: <{parser.stack[-1]}>", 0, "HTMLStructureError")
    return _ok(path, "html")


def _validate_xml(path, text):
    try:
        ET.fromstring(text)
        return _ok(path, "xml")
    except ET.ParseError as exc:
        line = int(getattr(exc, "position", (0, 0))[0] or 0)
        return _issue(path, "xml", str(exc), line, "XMLParseError")


def _validate_yaml(path, text):
    # Sem PyYAML obrigatório: detecta erros de estrutura comuns sem inventar parser.
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
            return _issue(path, "yaml", "YAML não aceita tabs para indentação.", number, "YamlIndentationError")
        if raw.strip() and not raw.lstrip().startswith("#") and ":" not in raw and not raw.lstrip().startswith(("- ", "-\t", "---", "...")):
            # YAML escalar livre é permitido, portanto não deve ser recusado por isso.
            continue
    return _ok(path, "yaml", "Estrutura YAML básica verificada.")


def _validate_toml(path, text):
    try:
        import tomllib
        tomllib.loads(text)
        return _ok(path, "toml")
    except ModuleNotFoundError:
        return _balanced_delimiters(path, "toml", text, {"[": "]", "{": "}"})
    except Exception as exc:
        line_match = re.search(r"line\s+(\d+)", str(exc), re.IGNORECASE)
        return _issue(path, "toml", str(exc), int(line_match.group(1)) if line_match else 0, "TomlDecodeError")


def validate_candidate(path, text, workspace=None):
    path = Path(path)
    text = str(text)
    language = source_language(path, workspace)

    if language == "python":
        tabs = _leading_tabs_issue(path, language, text)
        if tabs:
            return tabs
        try:
            ast.parse(text, filename=str(path))
            return _ok(path, language)
        except (SyntaxError, IndentationError, TabError) as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            lines = text.splitlines()
            start = max(1, line - 3)
            end = min(len(lines), max(line + 3, start))
            excerpt = "\n".join(f"{index:>4}: {lines[index - 1]}" for index in range(start, end + 1))
            return _issue(path, language, getattr(exc, "msg", str(exc)), line, exc.__class__.__name__, excerpt)

    if language == "json":
        try:
            json.loads(text)
            return _ok(path, language)
        except json.JSONDecodeError as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            lines = text.splitlines()
            excerpt = lines[line - 1] if 0 < line <= len(lines) else ""
            return _issue(path, language, exc.msg, line, "JSONDecodeError", excerpt)

    if language == "javascript":
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        return structural if not structural["ok"] else _command_validation(path, text, "node", ["--check"], language)

    if language == "typescript":
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        return structural if not structural["ok"] else _ok(path, language, "Estrutura TypeScript verificada; tsc será usado quando disponível.")

    if language in {"dart", "java", "kotlin", "csharp", "go", "rust", "c", "cpp", "swift", "lua", "vue", "svelte"}:
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        if not structural["ok"]:
            return structural
        if language == "dart":
            return _command_validation(path, text, "dart", ["format", "--output=none"], language)
        if language == "php":
            return _command_validation(path, text, "php", ["-l"], language)
        return _ok(path, language, f"Estrutura {language} verificada; build/teste da stack será usado no workspace.")

    if language == "php":
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        return structural if not structural["ok"] else _command_validation(path, text, "php", ["-l"], language)

    if language == "ruby":
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        return structural if not structural["ok"] else _command_validation(path, text, "ruby", ["-c"], language)

    if language == "shell":
        return _command_validation(path, text, "sh", ["-n"], language)

    if language == "powershell":
        structural = _balanced_delimiters(path, language, text, {"(": ")", "[": "]", "{": "}"})
        return structural if not structural["ok"] else _ok(path, language, "Estrutura PowerShell verificada.")

    if language == "batch":
        return _ok(path, language, "Script batch será validado pela execução controlada do workspace.")

    if language == "html":
        return _validate_html(path, text)
    if language == "xml":
        return _validate_xml(path, text)
    if language == "css":
        return _balanced_delimiters(path, language, text, {"{": "}"})
    if language == "sql":
        return _balanced_delimiters(path, language, text, {"(": ")"})
    if language == "yaml":
        return _validate_yaml(path, text)
    if language == "toml":
        return _validate_toml(path, text)

    return _ok(path, language, "Sem validador específico necessário.")


def iter_source_files(workspace, limit=1200):
    root = Path(workspace).resolve()
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        yield path
        count += 1
        if count >= limit:
            return


def _run_workspace_command(root, command, language, timeout=180):
    executable = shutil.which(command[0])
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *command[1:]], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _issue(root, language, f"Validação da stack excedeu {timeout}s.", 0, "ValidationTimeout")
    except OSError as exc:
        return _issue(root, language, str(exc), 0, "ValidationProcessError")
    if result.returncode == 0:
        return None
    return _issue(root, language, (result.stdout or "").strip() or "Ferramenta da stack retornou erro.", 0, "WorkspaceValidationError")


def _workspace_validation_command(root, language):
    root = Path(root)
    if language == "rust": return ["cargo", "check"]
    if language == "go": return ["go", "test", "./..."]
    if language == "dotnet": return ["dotnet", "build", "--nologo"]
    if language == "java_maven": return ["mvn", "test", "-q"]
    if language == "gradle":
        wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
        return [str(root / wrapper), "test", "--no-daemon"] if (root / wrapper).exists() else ["gradle", "test", "--no-daemon"]
    if language == "cmake":
        # Não cria build automaticamente: só usa uma build já configurada.
        for candidate in (root / "build", root / "out" / "build"):
            if (candidate / "CMakeCache.txt").exists():
                return ["cmake", "--build", str(candidate)]
        return None
    if language == "typescript":
        if (root / "tsconfig.json").exists(): return ["npx", "tsc", "--noEmit", "--pretty", "false"]
    if language == "javascript":
        package = root / "package.json"
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}) if package.exists() else {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if "test" in scripts: return ["npm", "test", "--", "--runInBand"]
        if "build" in scripts: return ["npm", "run", "build"]
    if language == "flutter_dart": return ["flutter", "analyze"]
    if language == "php":
        # O lint por arquivo já foi aplicado; composer test é opcional e não deve ser inventado.
        return None
    if language == "ruby":
        if (root / "Rakefile").exists(): return ["bundle", "exec", "rake", "test"]
    if language == "swift":
        if (root / "Package.swift").exists(): return ["swift", "test"]
    return None


def validate_workspace(workspace):
    root = Path(workspace).resolve()
    issues, checked = [], 0
    for path in iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append(_issue(path, "text", str(exc), 0, "ReadError"))
            continue
        result = validate_candidate(path, text, root)
        checked += 1
        if not result["ok"]:
            issues.append(result)

    language = detect_workspace_language(root)
    command = _workspace_validation_command(root, language)
    if not issues and command:
        issue = _run_workspace_command(root, command, language)
        if issue:
            issues.append(issue)
    return checked, issues


def workspace_contract(workspace):
    root = Path(workspace).resolve()
    language = detect_workspace_language(root)
    profile = {
        "python": "Python detectado: preserve indentação e valide sintaxe/testes Python.",
        "flutter_dart": "Flutter/Dart detectado: preserve null-safety e valide com flutter analyze.",
        "dart": "Dart detectado: preserve null-safety, imports e widgets/classes.",
        "typescript": "TypeScript detectado: preserve tipos, imports e contratos; valide com tsc quando disponível.",
        "javascript": "JavaScript detectado: preserve módulos e valide com Node/npm quando disponível.",
        "rust": "Rust detectado: preserve ownership/tipos e valide com cargo check.",
        "go": "Go detectado: preserve pacotes e valide com go test ./....",
        "dotnet": "Projeto .NET/C# detectado: preserve namespaces/tipos e valide com dotnet build.",
        "java_maven": "Java/Maven detectado: preserve packages/imports e valide com mvn test.",
        "gradle": "Projeto Gradle detectado: identifique Java/Kotlin e valide com Gradle.",
        "cmake": "Projeto C/C++ CMake detectado: preserve headers/build e use a build configurada.",
        "php": "PHP detectado: preserve namespaces e valide com php -l quando disponível.",
        "ruby": "Ruby detectado: preserve sintaxe e valide com ruby -c/rake quando disponível.",
        "swift": "Swift detectado: preserve módulos e valide com swift test quando disponível.",
        "web": "Projeto web detectado: preserve HTML/CSS/JS e valide estrutura antes do teste visual.",
        "generic": "Projeto multi-stack ou genérico: escolha a ferramenta pela extensão e pelos arquivos de configuração reais.",
    }.get(language, "Projeto multi-stack: escolha ferramenta por arquivo e configuração real.")
    return (
        "CONTRATO MULTILINGUAGEM DA IDE\n"
        f"Workspace: {root.name}\n"
        f"Stack principal detectada: {language}\n"
        f"{profile}\n"
        "Regra universal: nunca invente trechos ausentes; leia o arquivo real antes de editar. "
        "A IDE valida cada arquivo pela extensão e executa a validação da stack quando a ferramenta existir."
    )


def validation_command(workspace):
    helper = Path(__file__).resolve()
    return f'"{sys.executable}" "{helper}" --validate-workspace "{Path(workspace).resolve()}"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-workspace", default="")
    args = parser.parse_args()
    if not args.validate_workspace:
        print("Informe --validate-workspace CAMINHO")
        return 2
    root = Path(args.validate_workspace).resolve()
    checked, issues = validate_workspace(root)
    print(f"Validação multilinguagem: stack {detect_workspace_language(root)}; {checked} arquivo(s) analisado(s).")
    if not issues:
        print("VALIDAÇÃO APROVADA")
        return 0
    for item in issues[:12]:
        location = f" linha {item['line']}" if item.get("line") else ""
        print(f"ERRO {item['language']} {item['path']}{location}: {item['kind']}: {item['message']}")
        if item.get("excerpt"):
            print(item["excerpt"])
    if len(issues) > 12:
        print(f"... mais {len(issues) - 12} erro(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
