"""Execucao Qt das acoes basicas emitidas pelo agente.

Mantem o protocolo de mensagens do modulo agent_actions, mas entrega os efeitos
ao adaptador PySide6 sem depender de widgets Tk.
"""

from __future__ import annotations

import re
from pathlib import Path

from modules.app_constants import is_ignored_dir_name


class QtAgentActions:
    def __init__(self, workspace, on_message, on_command, on_changed):
        self.workspace = Path(workspace).resolve()
        self.on_message = on_message
        self.on_command = on_command
        self.on_changed = on_changed

    def _path(self, raw):
        path = (self.workspace / str(raw).strip()).resolve()
        if path != self.workspace and self.workspace not in path.parents:
            raise ValueError("A acao tentou sair do workspace.")
        return path

    def apply(self, response):
        changed = []
        for patch in re.findall(r"\[PATCH(?:\s*:[^\]]+)?\](.*?)\[/PATCH\]", response, re.I | re.S):
            changed.extend(self._patch(patch))
        for request in re.findall(r"\[READ:\s*([^\]]+)\]", response, re.I):
            self._read(request)
        for request in re.findall(r"\[SEARCH_TEXT:\s*([^\]]+)\]", response, re.I):
            self._search(request)
        for raw_path, content in re.findall(r"\[WRITE:\s*([^\]\r\n]+)\](.*?)\[/WRITE\]", response, re.I | re.S):
            try:
                path = self._path(raw_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content.strip("\r\n") + "\n", encoding="utf-8")
                changed.append(path)
                self.on_message("Sistema", f"Arquivo gravado pela IA: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self.on_message("Erro", f"WRITE recusado: {exc}")
        for raw_path, body in re.findall(r"\[REPLACE:\s*([^\]]+)\](.*?)\[/REPLACE\]", response, re.I | re.S):
            old = re.search(r"\[OLD\](.*?)\[/OLD\]", body, re.I | re.S)
            new = re.search(r"\[NEW\](.*?)\[/NEW\]", body, re.I | re.S)
            try:
                if not old or not new:
                    raise ValueError("REPLACE precisa de OLD e NEW.")
                path = self._path(raw_path)
                source = path.read_text(encoding="utf-8")
                old_text = old.group(1).strip("\r\n")
                if old_text not in source:
                    raise ValueError("Trecho OLD nao encontrado; arquivo preservado.")
                path.write_text(source.replace(old_text, new.group(1).strip("\r\n"), 1), encoding="utf-8")
                changed.append(path)
                self.on_message("Sistema", f"Arquivo atualizado pela IA: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self.on_message("Erro", f"REPLACE recusado: {exc}")
        for command in re.findall(r"\[EXECUTE:\s*([^\]\r\n]+)\]", response, re.I):
            self.on_command(command.strip())
        if changed:
            self.on_changed(changed)
        return changed

    def _patch(self, raw):
        raw = raw.strip().removeprefix("```diff").removeprefix("```patch").removesuffix("```").strip()
        if "*** Begin Patch" not in raw or "*** End Patch" not in raw:
            self.on_message("Erro", "PATCH recusado: formato esperado ausente.")
            return []
        body = raw.split("*** Begin Patch", 1)[1].split("*** End Patch", 1)[0]
        sections, kind, path, lines = [], None, None, []
        for line in body.splitlines():
            found = re.match(r"\*\*\* (Update File|Add File|Delete File):\s*(.+)", line)
            if found:
                if kind:
                    sections.append((kind, path, lines))
                kind, path, lines = found.group(1), found.group(2).strip(), []
            elif kind:
                lines.append(line)
        if kind:
            sections.append((kind, path, lines))
        changed = []
        try:
            for kind, raw_path, lines in sections:
                target = self._path(raw_path)
                if kind == "Add File":
                    if target.exists(): raise ValueError(f"Arquivo ja existe: {raw_path}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("\n".join(line[1:] for line in lines if line.startswith("+")) + "\n", encoding="utf-8")
                elif kind == "Delete File":
                    if not target.exists(): raise ValueError(f"Arquivo nao existe: {raw_path}")
                    target.unlink()
                else:
                    source = target.read_text(encoding="utf-8")
                    source_lines, final_newline = source.splitlines(), source.endswith("\n")
                    chunks = re.split(r"^@@.*$", "\n".join(lines), flags=re.M)[1:]
                    if not chunks: raise ValueError(f"PATCH sem hunk: {raw_path}")
                    for chunk in chunks:
                        hunk = [line for line in chunk.splitlines() if line.startswith((" ", "+", "-"))]
                        old = [line[1:] for line in hunk if line.startswith((" ", "-"))]
                        new = [line[1:] for line in hunk if line.startswith((" ", "+"))]
                        for index in range(len(source_lines) - len(old) + 1):
                            if source_lines[index:index + len(old)] == old:
                                source_lines[index:index + len(old)] = new; break
                        else: raise ValueError(f"Contexto nao encontrado: {raw_path}")
                    target.write_text("\n".join(source_lines) + ("\n" if final_newline else ""), encoding="utf-8")
                changed.append(target)
                self.on_message("Sistema", f"PATCH aplicado: {target.relative_to(self.workspace)}")
        except (OSError, ValueError) as exc:
            self.on_message("Erro", f"PATCH recusado: {exc}")
            return []
        return changed

    def _read(self, request):
        raw_path, _, range_text = request.partition("|")
        try:
            path = self._path(raw_path)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start, end = 1, min(len(lines), 240)
            match = re.search(r"linhas?\s*(\d+)\s*-\s*(\d+)", range_text, re.I)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
            start = max(1, start)
            end = min(len(lines), max(start, end), start + 239)
            content = "\n".join(f"{number:>5}  {lines[number - 1]}" for number in range(start, end + 1))
            self.on_message("Leitura da IDE", f"{path.relative_to(self.workspace)} (linhas {start}-{end})\n{content}")
        except (OSError, ValueError) as exc:
            self.on_message("Erro", f"READ recusado: {exc}")

    def _search(self, request):
        term, separator, raw_path = request.partition("|")
        term = term.strip()
        if not term:
            self.on_message("Erro", "SEARCH_TEXT precisa de um termo.")
            return
        try:
            roots = [self._path(raw_path)] if separator and raw_path.strip() else list(self.workspace.rglob("*"))
            results = []
            for path in roots:
                if (
                    not path.is_file()
                    or path.suffix.lower() in {".pyc", ".dll", ".exe", ".zip"}
                    or any(is_ignored_dir_name(part) for part in path.relative_to(self.workspace).parts[:-1])
                ):
                    continue
                try:
                    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if term.casefold() in line.casefold():
                            results.append(f"{path.relative_to(self.workspace)}:{number}: {line.strip()[:220]}")
                            if len(results) >= 80:
                                break
                except OSError:
                    continue
                if len(results) >= 80:
                    break
            message = "\n".join(results) if results else "Nenhuma ocorrencia encontrada."
            self.on_message("Busca da IDE", f"SEARCH_TEXT: {term}\n{message}")
        except ValueError as exc:
            self.on_message("Erro", f"SEARCH_TEXT recusado: {exc}")
