"""Compatibilidade para a barreira de integridade da Merotec IA IDE.

Historicamente este módulo só verificava Python e JSON. Agora ele delega para o
validador multilinguagem, mantendo as funções e a CLI antigas para instalações
que ainda as chamam.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from modules.language_guard import validate_candidate, validate_workspace


def unwrap_outer_fence(content):
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = text.split("\n")
    if len(lines) >= 2 and lines[0].lstrip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return text


def validate_source(path, content, workspace=None):
    """Retorna ``None`` quando o conteúdo é válido, como a API antiga."""
    result = validate_candidate(Path(path), str(content or ""), workspace)
    if result.get("ok"):
        return None
    return {
        "path": result.get("path", str(path)),
        "kind": result.get("kind", "ValidationError"),
        "message": result.get("message", "Validação recusada."),
        "line": int(result.get("line") or 0),
        "excerpt": result.get("excerpt", ""),
        "language": result.get("language", "text"),
    }


def validate_workspace_python(workspace):
    """Nome legado: agora valida o workspace inteiro por linguagem."""
    checked, failures = validate_workspace(Path(workspace).resolve())
    return checked, [
        {
            "path": item.get("path", ""),
            "kind": item.get("kind", "ValidationError"),
            "message": item.get("message", "Validação recusada."),
            "line": int(item.get("line") or 0),
            "excerpt": item.get("excerpt", ""),
            "language": item.get("language", "text"),
        }
        for item in failures
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-python", default="")
    parser.add_argument("--validate-workspace", default="")
    args = parser.parse_args()
    workspace = args.validate_workspace or args.validate_python
    if not workspace:
        print("Use --validate-workspace CAMINHO_DO_WORKSPACE")
        return 2
    checked, failures = validate_workspace_python(workspace)
    print(f"Validação multilinguagem: {checked} arquivo(s) verificado(s).")
    if not failures:
        print("VALIDAÇÃO APROVADA")
        return 0
    for issue in failures[:12]:
        location = f", linha {issue['line']}" if issue.get("line") else ""
        language = issue.get("language", "text")
        print(f"ERRO {language} {issue['path']}{location}: {issue['kind']}: {issue['message']}")
        if issue.get("excerpt"):
            print(issue["excerpt"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
