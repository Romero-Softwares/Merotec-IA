"""Execucao Qt das acoes basicas emitidas pelo agente.

Mantem o protocolo de mensagens do modulo agent_actions, mas entrega os efeitos
ao adaptador PySide6 sem depender de widgets Tk.
"""

from __future__ import annotations

import re
from pathlib import Path

from modules.app_constants import is_ignored_dir_name
from modules.code_transport import unwrap_transport_code, validate_file, validate_source_text


class QtAgentActions:
    def __init__(
        self,
        workspace,
        on_message,
        on_command,
        on_changed,
        task_objective="",
        write_staging=None,
        on_human_test=None,
        on_browser_action=None,
    ):
        self.workspace = Path(workspace).resolve()
        self.on_message = on_message
        self.on_command = on_command
        self.on_changed = on_changed
        self.task_objective = str(task_objective or "")
        self.write_staging = write_staging if write_staging is not None else {}
        self.last_observations = []
        self.last_action_count = 0
        self.last_followup_required = 0
        self.last_validated_paths = []
        self.last_command_requested = False
        self.last_visual_test_requested = False
        self.last_browser_action_requested = False
        self.on_human_test = on_human_test
        self.on_browser_action = on_browser_action

    def _message(self, author, text):
        self.last_observations.append(f"{author}: {text}")
        self.on_message(author, text)

    def _path(self, raw):
        path = (self.workspace / str(raw).strip()).resolve()
        if path != self.workspace and self.workspace not in path.parents:
            raise ValueError("A acao tentou sair do workspace.")
        return path

    def _mutation_recovery(self, raw_path):
        """Devolve o estado real após uma edição recusada ao Chat Web.

        Sem esse contexto o modelo tende a procurar trechos que não existem em
        um arquivo já truncado. Para arquivos pequenos, entregar o fonte atual
        permite que ele escolha um WRITE completo seguro na próxima ação.
        """
        try:
            path = self._path(raw_path)
            if not path.is_file():
                return
            source = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(self.workspace).as_posix()
            issue = validate_file(path)
            explicit_full_write = bool(re.search(
                r"(?i)\b(?:sobrescrev\w*|reescrev\w*|recri\w*|substitu\w*\s+(?:o\s+)?arquivo|"
                r"arquivo\s+inteiro|arquivo\s+completo|ger(?:e|ar)\s+(?:um\s+)?novo)\b",
                self.task_objective,
            ))
            if len(source) > 18000:
                self._message(
                    "Recuperação da IDE",
                    f"{relative} permanece inalterado. Use [READ: {relative}] para obter o trecho atual antes de tentar nova edição.",
                )
                return
            if issue is None:
                if explicit_full_write:
                    self._message(
                        "Recuperação da IDE",
                        f"A tarefa atual pede explicitamente sobrescrever {relative}. "
                        f"Responda agora com [WRITE: {relative}] e o arquivo COMPLETO novo; "
                        "não use REPLACE. A IDE salvará assim que o conteúdo estiver estruturalmente válido.",
                    )
                    return
                self._message(
                    "Recuperação da IDE",
                    f"{relative} permanece válido, mas a alteração pedida AINDA NÃO foi aplicada. "
                    f"Use [REPLACE: {relative}] com [OLD] copiado exatamente do ARQUIVO ATUAL abaixo e um [NEW] pequeno; "
                    "não reescreva o arquivo inteiro e não conclua a tarefa antes de aplicar a mudança.\n"
                    f"ARQUIVO ATUAL — {relative}:\n```\n{source}\n```",
                )
                return
            self._message(
                "Recuperação da IDE",
                f"O arquivo {relative} permanece inalterado e este é seu conteúdo real. "
                f"Se ele estiver incompleto, responda na próxima ação com [WRITE: {relative}] contendo o arquivo COMPLETO; "
                "não repita REPLACE com um trecho OLD diferente.\n"
                f"ARQUIVO ATUAL — {relative}:\n```\n{source}\n```",
            )
        except (OSError, ValueError):
            return

    def apply(self, response):
        changed = []
        self.last_observations = []
        self.last_action_count = 0
        self.last_followup_required = 0
        self.last_validated_paths = []
        self.last_command_requested = False
        self.last_visual_test_requested = False
        self.last_browser_action_requested = False
        for patch in re.findall(r"\[PATCH(?:\s*:[^\]]+)?\](.*?)\[/PATCH\]", response, re.I | re.S):
            self.last_action_count += 1
            self.last_followup_required += 1
            changed.extend(self._patch(patch))
        for request in re.findall(r"\[READ:\s*([^\]]+)\]", response, re.I):
            self.last_action_count += 1
            self.last_followup_required += 1
            self._read(request)
        for request in re.findall(r"\[SEARCH_TEXT:\s*([^\]]+)\]", response, re.I):
            self.last_action_count += 1
            self.last_followup_required += 1
            self._search(request)
        for raw_path, part, total, content in re.findall(
            r"\[WRITE_PART:\s*([^|\]\r\n]+)\|\s*(\d+)\s*/\s*(\d+)\s*\](.*?)\[/WRITE_PART\]",
            response, re.I | re.S,
        ):
            self.last_action_count += 1
            self.last_followup_required += 1
            try:
                path = self._path(raw_path)
                part, total = int(part), int(total)
                if not 1 <= part <= total <= 80:
                    raise ValueError("numeração inválida")
                key = str(path)
                stage = self.write_staging.setdefault(key, {"total": total, "raw_path": raw_path.strip(), "parts": {}})
                if stage["total"] != total:
                    raise ValueError("o total de partes mudou")
                fragment = content.strip("\r\n")
                previous = stage["parts"].get(part)
                if previous is not None and previous != fragment:
                    raise ValueError(f"parte {part} conflitante")
                stage["parts"][part] = fragment
                if len(stage["parts"]) < total:
                    self._message("Sistema", f"WRITE_PART recebido: {path.name} ({len(stage['parts'])}/{total}).")
                    continue
                source = "\n".join(stage["parts"][number] for number in range(1, total + 1))
                del self.write_staging[key]
                source = unwrap_transport_code(source, path)
                issue = validate_source_text(path, source)
                if issue:
                    raise ValueError(f"Conteúdo inválido: {issue.get('message') or issue.get('kind')}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source.rstrip("\n") + "\n", encoding="utf-8")
                changed.append(path)
                self._message("Sistema", f"Arquivo gravado pela IA em {total} partes: {path.relative_to(self.workspace)}")
            except (OSError, ValueError, KeyError) as exc:
                self._message("Erro", f"WRITE_PART recusado: {exc}")
                self._mutation_recovery(raw_path)
        for raw_path, content in re.findall(r"\[WRITE:\s*([^\]\r\n]+)\](.*?)\[/WRITE\]", response, re.I | re.S):
            self.last_action_count += 1
            self.last_followup_required += 1
            try:
                path = self._path(raw_path)
                source = unwrap_transport_code(content, path)
                issue = validate_source_text(path, source)
                if issue:
                    raise ValueError(
                        f"Conteúdo inválido para {path.name} na linha {issue.get('line') or '?'}: "
                        f"{issue.get('message') or issue.get('kind')}."
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source.rstrip("\n") + "\n", encoding="utf-8")
                changed.append(path)
                self._message("Sistema", f"Arquivo gravado pela IA: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self._message("Erro", f"WRITE recusado: {exc}")
                self._mutation_recovery(raw_path)
        for raw_path, body in re.findall(r"\[REPLACE:\s*([^\]]+)\](.*?)\[/REPLACE\]", response, re.I | re.S):
            self.last_action_count += 1
            self.last_followup_required += 1
            old = re.search(r"\[OLD\](.*?)\[/OLD\]", body, re.I | re.S)
            new = re.search(r"\[NEW\](.*?)\[/NEW\]", body, re.I | re.S)
            try:
                if not old or not new:
                    raise ValueError("REPLACE precisa de OLD e NEW.")
                path = self._path(raw_path)
                source = path.read_text(encoding="utf-8")
                old_text = unwrap_transport_code(old.group(1), path).strip("\r\n")
                if old_text not in source:
                    raise ValueError("Trecho OLD nao encontrado; arquivo preservado.")
                replacement = unwrap_transport_code(new.group(1), path).strip("\r\n")
                issue = validate_source_text(path, source.replace(old_text, replacement, 1))
                if issue:
                    raise ValueError(
                        f"Conteúdo inválido para {path.name} na linha {issue.get('line') or '?'}: "
                        f"{issue.get('message') or issue.get('kind')}."
                    )
                path.write_text(source.replace(old_text, replacement, 1), encoding="utf-8")
                changed.append(path)
                self._message("Sistema", f"Arquivo atualizado pela IA: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self._message("Erro", f"REPLACE recusado: {exc}")
                self._mutation_recovery(raw_path)
        for raw_path, marker, content in re.findall(
            r"\[INSERT_BEFORE\s*:\s*([^|\]\r\n]+)\|\s*([^\]\r\n]+)\](.*?)\[/INSERT_BEFORE\]",
            response, re.I | re.S,
        ):
            self.last_action_count += 1
            self.last_followup_required += 1
            try:
                path = self._path(raw_path)
                source = path.read_text(encoding="utf-8")
                marker = marker.strip()
                if not marker or source.count(marker) != 1:
                    raise ValueError("Marcador ausente ou ambíguo; arquivo preservado.")
                addition = unwrap_transport_code(content, path).strip("\r\n")
                candidate = source.replace(marker, addition + "\n" + marker, 1)
                issue = validate_source_text(path, candidate)
                if issue:
                    raise ValueError(f"Conteúdo inválido: {issue.get('message') or issue.get('kind')}")
                path.write_text(candidate, encoding="utf-8")
                changed.append(path)
                self._message("Sistema", f"Conteúdo acrescentado antes de {marker}: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self._message("Erro", f"INSERT_BEFORE recusado: {exc}")
                self._mutation_recovery(raw_path)
        for raw_path, marker, content in re.findall(
            r"\[INSERT_AFTER\s*:\s*([^|\]\r\n]+)\|\s*([^\]\r\n]+)\](.*?)\[/INSERT_AFTER\]",
            response, re.I | re.S,
        ):
            self.last_action_count += 1
            self.last_followup_required += 1
            try:
                path = self._path(raw_path)
                source = path.read_text(encoding="utf-8")
                marker = marker.strip()
                if not marker or source.count(marker) != 1:
                    raise ValueError("Marcador ausente ou ambíguo; arquivo preservado.")
                addition = unwrap_transport_code(content, path).strip("\r\n")
                candidate = source.replace(marker, marker + "\n" + addition, 1)
                issue = validate_source_text(path, candidate)
                if issue:
                    raise ValueError(f"Conteúdo inválido: {issue.get('message') or issue.get('kind')}")
                path.write_text(candidate, encoding="utf-8")
                changed.append(path)
                self._message("Sistema", f"Conteúdo acrescentado após {marker}: {path.relative_to(self.workspace)}")
            except (OSError, ValueError) as exc:
                self._message("Erro", f"INSERT_AFTER recusado: {exc}")
                self._mutation_recovery(raw_path)
        for raw_path in re.findall(r"\[VALIDATE\s*:\s*([^\]\r\n]+)\]", response, re.I):
            self.last_action_count += 1
            self.last_followup_required += 1
            try:
                path = self._path(raw_path)
                if not path.is_file():
                    raise ValueError("Arquivo não encontrado.")
                issue = validate_file(path)
                relative = path.relative_to(self.workspace)
                if issue and issue.get("kind") == "TransportArtifact":
                    source = path.read_text(encoding="utf-8")
                    repaired = unwrap_transport_code(source, path)
                    repair_issue = validate_source_text(path, repaired)
                    if repair_issue is None and repaired != source:
                        path.write_text(repaired.rstrip("\n") + "\n", encoding="utf-8")
                        changed.append(path)
                        issue = None
                        self._message("Sistema", f"Artefatos de transporte removidos automaticamente: {relative}")
                if issue:
                    raise ValueError(
                        f"Validação falhou ({issue.get('kind')}): {issue.get('message')}"
                    )
                self.last_validated_paths.append(path.resolve())
                self._message("Validação da IDE", f"{relative}: arquivo válido.")
            except (OSError, ValueError) as exc:
                self._message("Erro", f"VALIDATE recusado: {exc}")
        # A tag é de uma linha, mas o comando pode conter colchetes (listas
        # Python, índices PowerShell etc.). Capture até o último ``]`` da linha.
        for request in re.findall(r"\[HUMAN_TEST\s*:\s*([^\]\r\n]*)\]", response, re.I):
            self.last_action_count += 1
            request = request.strip() or "auto"
            if not callable(self.on_human_test):
                self._message("Erro", "HUMAN_TEST indisponivel nesta interface.")
                continue
            try:
                started = self.on_human_test(request)
            except Exception as exc:
                self._message("Erro", f"HUMAN_TEST nao iniciado: {exc}")
                continue
            if started is False:
                self._message("Erro", "HUMAN_TEST nao iniciado.")
                continue
            self.last_visual_test_requested = True
            self._message("Sistema", "Teste visual iniciado; aguardando abertura e captura da interface.")
        browser_requests = []
        for match in re.finditer(
            r"\[(OPEN_URL|BROWSER_INSPECT|BROWSER_CLICK|BROWSER_TYPE|BROWSER_SCROLL)\s*:\s*([^\]\r\n]+)\]",
            response,
            re.I,
        ):
            action = match.group(1).upper()
            value = match.group(2).strip()
            if action == "OPEN_URL":
                browser_requests.append(("open", {"url": value}, match.start()))
            elif action == "BROWSER_INSPECT":
                browser_requests.append(("inspect", {}, match.start()))
            elif action == "BROWSER_CLICK":
                browser_requests.append(("click", {"target": value}, match.start()))
            elif action == "BROWSER_SCROLL":
                browser_requests.append(("scroll", {"target": value.lower()}, match.start()))
            else:
                target, separator, typed = value.partition("|")
                if not separator or not target.strip():
                    self._message("Erro", "BROWSER_TYPE precisa usar: elemento | texto.")
                    continue
                browser_requests.append(
                    ("type", {"target": target.strip(), "value": typed.strip()}, match.start())
                )
        if browser_requests:
            # Uma acao por resposta preserva a ordem do protocolo: abrir, ler a
            # pagina e interagir sao etapas assincronas que precisam devolver o
            # DOM real ao agente antes da proxima decisao.
            action, payload, _position = min(browser_requests, key=lambda item: item[2])
            self.last_action_count += 1
            if not callable(self.on_browser_action):
                self._message("Erro", "Navegador interno indisponivel nesta interface.")
            else:
                try:
                    started = self.on_browser_action(action, payload)
                except Exception as exc:
                    self._message("Erro", f"Acao do navegador nao iniciada: {exc}")
                else:
                    if started is False:
                        self._message("Erro", "Acao do navegador nao iniciada.")
                    else:
                        self.last_browser_action_requested = True
                        self._message("Sistema", f"Acao do navegador iniciada: {action}.")
        for command in re.findall(r"(?im)^\s*\[EXECUTE\s*:\s*(.+)\]\s*$", response):
            self.last_action_count += 1
            command = command.strip()
            if command:
                started = self.on_command(command)
                if started is False:
                    self._message("Erro", f"EXECUTE não iniciado: {command}")
                else:
                    self.last_command_requested = True
                    self._message("Sistema", f"EXECUTE encaminhado ao terminal: {command}")
            else:
                self._message("Erro", "EXECUTE recusado: comando vazio.")
        if changed:
            self.on_changed(changed)
        return changed

    def _patch(self, raw):
        raw = raw.strip().removeprefix("```diff").removeprefix("```patch").removesuffix("```").strip()
        if "*** Begin Patch" not in raw or "*** End Patch" not in raw:
            self._message("Erro", "PATCH recusado: formato esperado ausente.")
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
                self._message("Sistema", f"PATCH aplicado: {target.relative_to(self.workspace)}")
        except (OSError, ValueError) as exc:
            self._message("Erro", f"PATCH recusado: {exc}")
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
            self._message("Leitura da IDE", f"{path.relative_to(self.workspace)} (linhas {start}-{end})\n{content}")
        except (OSError, ValueError) as exc:
            self._message("Erro", f"READ recusado: {exc}")

    def _search(self, request):
        term, separator, raw_path = request.partition("|")
        term = term.strip()
        if not term:
            self._message("Erro", "SEARCH_TEXT precisa de um termo.")
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
            self._message("Busca da IDE", f"SEARCH_TEXT: {term}\n{message}")
        except ValueError as exc:
            self._message("Erro", f"SEARCH_TEXT recusado: {exc}")
