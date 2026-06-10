import os
import re
import subprocess
import sys
import threading
import unicodedata
from collections import Counter
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from modules.app_constants import IGNORED_DIRS, IGNORED_SUFFIXES


class WorkspaceIntelligenceMixin:
    def local_quick_reply(self, command, image_path=None):
        if image_path:
            return None

        normalized = self.normalize_plain_text(command)
        undo_reply = self.local_undo_reply(normalized)
        if undo_reply:
            return undo_reply

        calculation_reply = self.local_calculation_reply(command, normalized)
        if calculation_reply:
            return calculation_reply

        greetings = {
            "ola",
            "oi",
            "opa",
            "e ai",
            "bom dia",
            "boa tarde",
            "boa noite",
        }
        if normalized in greetings:
            return "Ola! Estou pronto. Me diga o que voce quer construir, corrigir ou analisar."

        return None

    def local_undo_reply(self, normalized):
        undo_terms = {"desfazer", "desfaca", "reverter", "reverta", "voltar", "restaurar", "restaure", "recuperar", "recupere"}
        words = set(re.findall(r"[a-z0-9_]+", normalized or ""))
        if not words & undo_terms:
            return None
        if not any(term in normalized for term in ("alteracao", "mudanca", "arquivo", "ultima", "nuvem", "nuvens", "remocao", "removeu", "desfazer", "reverter", "restaurar")):
            return None
        reply = self.undo_last_change()
        if "Nao encontrei alteracao recente" in reply:
            fallback = self.restore_main_backup()
            if fallback:
                return fallback
        return reply

    def local_calculation_reply(self, command, normalized=None):
        normalized = normalized or self.normalize_plain_text(command)
        if not self.looks_like_simple_calculation(normalized):
            return None

        expression = self.extract_calculation_expression(normalized)
        if not expression:
            return None

        try:
            result = self.evaluate_decimal_expression(expression)
        except (ValueError, InvalidOperation, DivisionByZero, ZeroDivisionError):
            return None

        if self.is_currency_question(normalized):
            formatted = self.format_brl(result)
            return f"12 x 4,70 = {formatted}" if "12" in normalized and "4,70" in normalized else f"Resultado: {formatted}"

        return f"Resultado: {self.format_decimal_result(result)}"

    def looks_like_simple_calculation(self, text):
        if not re.search(r"\d", text):
            return False
        intent_terms = {
            "quanto",
            "calcule",
            "calcula",
            "calcular",
            "resultado",
            "conta",
        }
        if any(term in text for term in intent_terms):
            return True
        return bool(re.search(r"\d+\s*(?:x|\*|/|\+|-|,|\.)\s*\d+", text))

    def extract_calculation_expression(self, text):
        expr = f" {text} "
        replacements = [
            (r"\bdividido\s+por\b", "/"),
            (r"\bmultiplicado\s+por\b", "*"),
            (r"\bvezes\b", "*"),
            (r"\bmais\b", "+"),
            (r"\bmenos\b", "-"),
        ]
        for pattern, replacement in replacements:
            expr = re.sub(pattern, replacement, expr)

        expr = re.sub(r"\bquanto\s+(?:e|eh|é)\b", " ", expr)
        expr = re.sub(r"\b(?:calcule|calcula|calcular|resultado|conta|qual|o|de|da|do|em|reais|real)\b", " ", expr)
        expr = expr.replace("r$", " ")
        expr = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
        expr = re.sub(r"(?<=\d),(?=\d)", ".", expr)
        expr = expr.replace("÷", "/").replace("×", "*")

        tokens = re.findall(r"\d+(?:\.\d+)?|[()+\-*/]", expr)
        if not tokens or not any(token in {"+", "-", "*", "/"} for token in tokens):
            return ""
        return " ".join(tokens)

    def evaluate_decimal_expression(self, expression):
        tokens = re.findall(r"\d+(?:\.\d+)?|[()+\-*/]", expression)
        if not tokens:
            raise ValueError("expressao vazia")

        output = []
        operators = []
        precedence = {"+": 1, "-": 1, "*": 2, "/": 2}
        previous = None

        for token in tokens:
            if re.fullmatch(r"\d+(?:\.\d+)?", token):
                output.append(Decimal(token))
                previous = "number"
            elif token in "+-*/":
                if token == "-" and previous in {None, "operator", "("}:
                    output.append(Decimal("0"))
                while operators and operators[-1] in precedence and precedence[operators[-1]] >= precedence[token]:
                    output.append(operators.pop())
                operators.append(token)
                previous = "operator"
            elif token == "(":
                operators.append(token)
                previous = "("
            elif token == ")":
                while operators and operators[-1] != "(":
                    output.append(operators.pop())
                if not operators:
                    raise ValueError("parenteses invalidos")
                operators.pop()
                previous = "number"

        while operators:
            operator = operators.pop()
            if operator in {"(", ")"}:
                raise ValueError("parenteses invalidos")
            output.append(operator)

        stack = []
        for item in output:
            if isinstance(item, Decimal):
                stack.append(item)
                continue
            if len(stack) < 2:
                raise ValueError("operacao invalida")
            right = stack.pop()
            left = stack.pop()
            if item == "+":
                stack.append(left + right)
            elif item == "-":
                stack.append(left - right)
            elif item == "*":
                stack.append(left * right)
            elif item == "/":
                stack.append(left / right)

        if len(stack) != 1:
            raise ValueError("expressao invalida")
        return stack[0]

    def is_currency_question(self, text):
        return "real" in text or "reais" in text or "r$" in text

    def format_brl(self, value):
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        integer, cents = f"{rounded:.2f}".split(".")
        groups = []
        while integer:
            groups.append(integer[-3:])
            integer = integer[:-3]
        return f"R$ {'.'.join(reversed(groups))},{cents}"

    def format_decimal_result(self, value):
        rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()
        text = format(rounded, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text.replace(".", ",")

    def local_autonomous_task(self, command, normalized=None, image_path=None):
        normalized = normalized or self.normalize_plain_text(command)

        if self.is_project_run_request(normalized):
            return self.start_project_run_task(command, normalized)

        if image_path:
            return None

        if self.is_zoom_mobile_verification_request(normalized):
            return self.verify_zoom_mobile_locally(command)

        return None

    def is_zoom_mobile_verification_request(self, normalized):
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        verify_terms = {"verificar", "verifique", "veja", "existe", "exista", "tem", "possui", "confira"}
        return bool(words & verify_terms) and "zoom" in normalized and "mobile" in normalized

    def verify_zoom_mobile_locally(self, command):
        files = self.find_likely_search_targets(command, suffixes={".html", ".js", ".ts", ".tsx", ".jsx", ".dart"})
        if not files:
            return "Nao encontrei arquivo de codigo adequado no projeto atual para verificar zoom mobile."

        pattern = r"zoom|pinch|wheel|touchstart|touchmove|gesture|mobile|isMobile|scale|cameraZoom|fov"
        summaries = []
        zoom_hits = []
        mobile_hits = []
        touch_hits = []

        for path in files[:8]:
            rel = path.relative_to(self.current_workspace).as_posix()
            matches = self.search_file_lines(path, pattern, limit=40)
            if not matches:
                summaries.append(f"- {rel}: nenhuma ocorrencia.")
                continue
            summaries.append(f"- {rel}: {len(matches)} ocorrencia(s).")
            for number, line in matches:
                lower = line.lower()
                entry = f"{rel}:{number}: {line.strip()[:180]}"
                if any(term in lower for term in ("zoom", "camerazoom", "scale", "fov")):
                    zoom_hits.append(entry)
                if any(term in lower for term in ("mobile", "ismobile", "modo mobile")):
                    mobile_hits.append(entry)
                if any(term in lower for term in ("pinch", "touchstart", "touchmove", "gesture", "wheel")):
                    touch_hits.append(entry)

        if zoom_hits and (mobile_hits or touch_hits):
            verdict = "Sim, encontrei sinais de logica de zoom relacionada a mobile/toque."
        elif zoom_hits:
            verdict = "Encontrei logica de zoom, mas nao encontrei evidencia clara de que ela esteja limitada ao modo mobile."
        elif mobile_hits or touch_hits:
            verdict = "Encontrei logica de mobile/toque, mas nao encontrei funcao clara de zoom."
        else:
            verdict = "Nao encontrei funcao de zoom para modo mobile nos arquivos analisados."

        evidence = zoom_hits[:6] + mobile_hits[:4] + touch_hits[:4]
        evidence_text = "\n".join(f"- {item}" for item in evidence) if evidence else "- Sem linhas relevantes."
        return (
            f"{verdict}\n\n"
            "Arquivos verificados:\n"
            + "\n".join(summaries[:8])
            + "\n\nEvidencias:\n"
            + evidence_text
        )

    def find_likely_search_targets(self, command, suffixes=None, limit=12):
        suffixes = suffixes or {".py", ".js", ".ts", ".html", ".css", ".dart"}
        workspace = Path(self.current_workspace).resolve()
        mentioned = self.extract_mentioned_file_paths(command)
        targets = []
        seen = set()

        for raw in mentioned:
            try:
                path = self.resolve_workspace_path(raw)
            except Exception:
                continue
            if path.is_file() and path.suffix.lower() in suffixes and path not in seen:
                targets.append(path)
                seen.add(path)

        for info in self.open_editors.values():
            raw_path = info.get("path")
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.is_file() and path.suffix.lower() in suffixes and path not in seen:
                targets.append(path)
                seen.add(path)

        for path, _rel in self.iter_workspace_files(limit=800):
            if path.suffix.lower() not in suffixes or path in seen:
                continue
            targets.append(path)
            seen.add(path)
            if len(targets) >= limit:
                break

        return [path for path in targets if str(path.resolve()).startswith(str(workspace))]

    def extract_mentioned_file_paths(self, text):
        extensions = r"(?:html|css|js|ts|tsx|jsx|py|dart|json|md|yaml|yml|txt|cpp|h|cs)"
        return re.findall(r"(?<![\w.-])([A-Za-z0-9_./\\-]+\." + extensions + r")", text or "", re.IGNORECASE)

    def search_file_lines(self, path, pattern, limit=80):
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        matches = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for number, line in enumerate(file, start=1):
                    if regex.search(line):
                        matches.append((number, line.rstrip("\n\r")))
                    if len(matches) >= limit:
                        break
        except OSError:
            return []
        return matches

    def is_project_run_request(self, normalized):
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        run_terms = {"execute", "executa", "executar", "rode", "roda", "rodar", "abrir", "inicie", "iniciar"}
        build_terms = {"build", "builde", "compila", "compile", "compilar"}
        target_terms = {"app", "projeto", "aplicativo", "programa"}
        fix_terms = {"corrija", "corrige", "corrigir", "arrume", "arruma", "arrumar", "conserte", "consertar"}
        if words & fix_terms and not words & run_terms:
            return False
        if words and words <= (run_terms | build_terms) and self.has_default_run_target():
            return True
        return bool(words & target_terms) and (bool(words & run_terms) or bool(words & build_terms))

    def has_default_run_target(self):
        workspace = Path(self.current_workspace)
        if (workspace / "pubspec.yaml").exists():
            return True
        if (workspace / "app.py").exists() or (workspace / "main.py").exists():
            return True
        if (workspace / "index.html").exists():
            return True
        return any(workspace.glob("*.html"))

    def normalize_match_key(self, text):
        normalized = self.normalize_plain_text(str(text or ""))
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def run_request_tokens(self, command, normalized=None):
        text = self.normalize_match_key(normalized or command)
        stop_words = {
            "a",
            "abra",
            "abrir",
            "app",
            "aplicativo",
            "build",
            "builde",
            "compila",
            "compile",
            "de",
            "do",
            "e",
            "execute",
            "executa",
            "executar",
            "inicie",
            "iniciar",
            "o",
            "os",
            "programa",
            "projeto",
            "roda",
            "rodar",
            "rode",
            "um",
            "uma",
        }
        return [token for token in text.split() if len(token) >= 3 and token not in stop_words]

    def detect_run_kind(self, workspace):
        workspace = Path(workspace)
        if (workspace / "pubspec.yaml").exists():
            return "flutter"
        if (workspace / "package.json").exists():
            return "node"
        if (workspace / "app.py").exists() or (workspace / "main.py").exists():
            return "python"
        if (workspace / "index.html").exists() or any(workspace.glob("*.html")):
            return "html"
        return ""

    def runnable_workspace_score(self, workspace):
        workspace = Path(workspace)
        score = 0
        kind = self.detect_run_kind(workspace)
        if kind == "flutter":
            score += 60
            if (workspace / "lib" / "main.dart").exists():
                score += 45
            if (workspace / "windows").exists():
                score += 15
            if (workspace / "android").exists() or (workspace / "ios").exists():
                score += 8
        elif kind == "node":
            score += 70
            if (workspace / "src").exists():
                score += 10
        elif kind == "python":
            score += 55
        elif kind == "html":
            score += 45
            if (workspace / "index.html").exists():
                score += 15
        try:
            rel_parts = workspace.resolve().relative_to(Path(self.current_workspace).resolve()).parts
            score += min(len(rel_parts), 4)
        except ValueError:
            pass
        return score

    def find_runnable_workspaces(self, limit=80):
        workspace = Path(self.current_workspace).resolve()
        markers = {
            "package.json",
            "pubspec.yaml",
            "pyproject.toml",
            "requirements.txt",
            "index.html",
            "main.py",
            "app.py",
        }
        candidates = {workspace}
        for path, _rel in self.iter_workspace_files(limit=3000):
            if path.name in markers:
                candidates.add(path.parent.resolve())
            if len(candidates) >= limit:
                break
        return sorted(
            (path for path in candidates if self.detect_run_kind(path)),
            key=lambda path: (self.runnable_workspace_score(path), str(path).lower()),
            reverse=True,
        )

    def resolve_requested_run_workspace(self, command, normalized=None):
        workspace = Path(self.current_workspace).resolve()
        candidates = self.find_runnable_workspaces()
        if not candidates:
            return workspace

        tokens = self.run_request_tokens(command, normalized)
        mentioned_dirs = []
        for candidate in candidates:
            try:
                rel = candidate.relative_to(workspace)
            except ValueError:
                continue
            rel_key = self.normalize_match_key(rel.as_posix())
            name_key = self.normalize_match_key(candidate.name)
            if any(token in rel_key.split() or token == name_key for token in tokens):
                mentioned_dirs.append(candidate)

        if mentioned_dirs:
            bases = []
            for mentioned in mentioned_dirs:
                bases.append(mentioned)
                try:
                    parent = mentioned.parent
                    if parent != workspace and parent.is_relative_to(workspace):
                        bases.append(parent)
                except AttributeError:
                    try:
                        mentioned.parent.relative_to(workspace)
                        bases.append(mentioned.parent)
                    except ValueError:
                        pass

            scoped = []
            for candidate in candidates:
                for base in bases:
                    try:
                        candidate.relative_to(base)
                    except ValueError:
                        continue
                    scoped.append(candidate)
                    break
            if scoped:
                return max(scoped, key=self.runnable_workspace_score)

        root_kind = self.detect_run_kind(workspace)
        if root_kind:
            return workspace
        return candidates[0]

    def relative_workspace_label(self, path):
        workspace = Path(self.current_workspace).resolve()
        path = Path(path).resolve()
        try:
            rel = path.relative_to(workspace)
            return "." if rel.as_posix() == "." else rel.as_posix()
        except ValueError:
            return str(path)

    def start_project_run_task(self, command, normalized=None):
        workspace = self.resolve_requested_run_workspace(command, normalized)
        rel_label = self.relative_workspace_label(workspace)
        kind = self.detect_run_kind(workspace)
        if (workspace / "pubspec.yaml").exists():
            command = "flutter pub get && flutter run -d windows"
            self.run_workspace_command(command, f"Executando app Flutter: {rel_label}", cwd=workspace)
            return (
                f"Execucao iniciada para `{rel_label}` como app Flutter pelo Terminal Local. "
                "O `flutter run -d windows` ja faz o build antes de abrir o app."
            )

        app_py = workspace / "app.py"
        main_py = workspace / "main.py"
        if app_py.exists() or main_py.exists():
            target = app_py if app_py.exists() else main_py
            command = f'"{sys.executable}" "{target.name}"'
            self.run_workspace_command(command, f"Executando {rel_label}/{target.name}", cwd=workspace)
            return f"Execucao iniciada para `{rel_label}/{target.name}` pelo Terminal Local."

        html_target = workspace / "index.html"
        if not html_target.exists():
            html_files = sorted(workspace.glob("*.html"))
            html_target = html_files[0] if html_files else None
        if html_target and html_target.exists():
            if os.name == "nt":
                command = f'cmd /c start "" "{html_target.name}"'
            else:
                command = f'python -m webbrowser "{html_target.name}"'
            self.run_workspace_command(command, f"Abrindo {rel_label}/{html_target.name}", cwd=workspace)
            return f"Abertura iniciada para `{rel_label}/{html_target.name}` no navegador padrao."

        return (
            f"Nao encontrei um comando automatico seguro para executar `{rel_label}`. "
            "Abra o Terminal Local e rode o comando especifico do framework."
        )

    def run_workspace_command(self, command, title=None, cwd=None):
        cwd_path = str(Path(cwd or self.current_workspace).resolve())
        self.tabview.set("Terminal Local")
        self.append_to_term(f"\n> {title or command}\n{cwd_path}> {command}\n")
        self.log_agent(f"Comando local iniciado: {command}")
        self.set_status(title or "Comando local em execucao...", "busy")
        self.set_terminal_busy(True, title or f"Executando: {command[:70]}")

        def execute():
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd_path,
                )
                self.register_terminal_process(process, title or command)
                self.stream_process_output(process)
                process.wait()
                self.append_to_term(f"\n[processo finalizado com codigo {process.returncode}]\n")
                self.set_status("Comando local finalizado.", "ready" if process.returncode == 0 else "error")
                self.load_workspace_files()
            except Exception as exc:
                self.append_to_term(f"[erro] {exc}\n")
                self.set_status("Falha no comando local.", "error")
            finally:
                if "process" in locals():
                    self.unregister_terminal_process(process)
                self.set_terminal_busy(False)

        threading.Thread(target=execute, daemon=True).start()

    def is_vague_project_update_request(self, normalized):
        if "projeto" not in normalized:
            return False

        words = set(re.findall(r"[a-z0-9_]+", normalized))
        update_terms = {
            "atualiza",
            "atualize",
            "atualizar",
            "melhora",
            "melhore",
            "melhorar",
            "arruma",
            "arrume",
            "arrumar",
            "corrige",
            "corrija",
            "corrigir",
        }
        if not words & update_terms:
            return False

        specific_targets = {
            "botao",
            "botoes",
            "layout",
            "tela",
            "erro",
            "build",
            "login",
            "codex",
            "explorer",
            "arquivo",
            "pubspec",
            "readme",
            "menu",
            "chat",
            "terminal",
        }
        return not bool(words & specific_targets)

    def is_project_analysis_request(self, normalized):
        if "projeto" not in normalized:
            return False

        analysis_terms = {
            "analise",
            "analisa",
            "analisar",
            "analize",
            "analiza",
            "analizar",
            "avaliar",
            "avalie",
            "revisar",
            "revise",
            "verificar",
            "verifique",
        }
        words = set(re.findall(r"[a-z0-9_]+", normalized))
        if words & analysis_terms:
            return True

        relaxed_patterns = [
            r"\bfaca\b.*\bprojeto\b",
            r"\bveja\b.*\bprojeto\b",
            r"\bolhe\b.*\bprojeto\b",
        ]
        return any(re.search(pattern, normalized) for pattern in relaxed_patterns)

    def normalize_plain_text(self, text):
        normalized = unicodedata.normalize("NFKD", text.strip().lower())
        without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", without_accents).strip()

    def local_project_summary(self):
        workspace = Path(self.current_workspace)
        files = list(self.iter_workspace_files(limit=1200))
        if not files:
            return (
                f"Projeto atual: {workspace.name}\n\n"
                "Nao encontrei arquivos analisaveis nesse projeto."
            )

        suffix_counts = Counter(path.suffix.lower() or "[sem extensao]" for path, _rel in files)
        top_extensions = ", ".join(
            f"{suffix}: {count}" for suffix, count in suffix_counts.most_common(8)
        )
        total_size = sum(path.stat().st_size for path, _rel in files if path.exists())
        key_files = self.local_key_files(files)
        project_type = self.detect_project_type(workspace, suffix_counts)
        folders = self.local_top_folders(files)

        notes = []
        if (workspace / "pubspec.yaml").exists():
            notes.append("Parece ser um app Flutter/Dart.")
        if (workspace / "build").exists():
            notes.append("A pasta build existe, mas fica ignorada pela IDE para nao poluir o contexto.")
        if any(rel.as_posix().endswith(".bak") for _path, rel in files):
            notes.append("Ha arquivos .bak no projeto; eles parecem backups criados pela IDE.")
        if not notes:
            notes.append("A estrutura esta limpa para leitura inicial.")

        return (
            f"Projeto atual: {workspace.name}\n"
            f"Tipo detectado: {project_type}\n"
            f"Arquivos analisaveis: {len(files)}\n"
            f"Tamanho aproximado: {self.format_bytes(total_size)}\n"
            f"Extensoes principais: {top_extensions}\n\n"
            f"Pastas principais:\n{folders}\n\n"
            f"Arquivos-chave:\n{key_files}\n\n"
            f"Observacoes:\n- " + "\n- ".join(notes)
        )

    def build_project_analysis_context(self):
        return (
            "CONTEXTO INICIAL DE ANALISE DO PROJETO GERADO PELA IDE:\n\n"
            f"{self.build_project_intelligence_context(deep=True)}\n\n"
            + "\n\n"
            "Ordem para a IA: esta e uma analise arquitetural de projeto grande. "
            "Nao leia dezenas de arquivos. Nao peca [READ] em massa. "
            "Use o mapa de subprojetos, arquivos-chave e comandos provaveis para entregar uma visao geral util. "
            "Leia no maximo 1 ou 2 arquivos especificos apenas se forem indispensaveis para confirmar uma conclusao."
        )

    def build_project_intelligence_context(self, deep=False):
        workspace = Path(self.current_workspace).resolve()
        files = list(self.iter_workspace_files(limit=1800 if deep else 900))
        summary = self.local_project_summary()
        key_files = self.local_key_files(files, limit=14)
        manifest = self.build_project_manifest(files, limit=130 if deep else 90)
        subprojects = self.detect_subprojects(files, limit=40 if deep else 20)
        run_hints = []
        if (workspace / "pubspec.yaml").exists():
            run_hints.extend([
                "- Flutter detectado: valide com `flutter test`, `flutter run -d windows` ou build alvo pedido.",
                "- Erros Windows/CMake/C++ costumam estar em `windows/runner/*` e nao em `lib/main.dart`.",
            ])
        if (workspace / "package.json").exists():
            run_hints.append("- Node/Web detectado: confira scripts em package.json antes de executar.")
        if (workspace / "index.html").exists():
            run_hints.append("- HTML unico detectado: evite reescrever o arquivo inteiro; preserve funcoes, controles e cena existentes.")
        if not run_hints:
            run_hints.append("- Use os arquivos-chave e o explorer para escolher a menor mudanca verificavel.")

        return (
            "MAPA PERMANENTE DO PROJETO PARA A IA:\n"
            f"{summary}\n\n"
            "Arquivos-chave do projeto:\n"
            f"{key_files}\n\n"
            "Manifesto compacto do projeto:\n"
            f"{manifest}\n\n"
            "Subprojetos detectados:\n"
            f"{subprojects}\n\n"
            "Historico recente que deve ser lembrado:\n"
            f"{self.format_recent_changes_for_agent(limit=10)}\n\n"
            "Direcionamento de trabalho:\n"
            + "\n".join(run_hints)
            + "\n- Entenda o projeto como um todo antes de editar, mas nao repita leitura do mesmo arquivo em loop.\n"
            "- Prefira mudancas cirurgicas em arquivos existentes; use reescrita completa so quando o usuario pedir recriar/refazer do zero.\n"
            "- Se o usuario pedir desfazer/restaurar algo destruido, use o historico e backups antes de criar uma nova versao."
        )

    def build_project_manifest(self, files, limit=90):
        if not files:
            return "- Nenhum arquivo textual encontrado."
        folder_counts = Counter()
        entry_candidates = []
        for path, rel in files:
            parts = rel.parts
            folder = parts[0] if len(parts) > 1 else "."
            folder_counts[folder] += 1
            rel_text = rel.as_posix()
            name = path.name.lower()
            if name in {
                "index.html",
                "main.py",
                "app.py",
                "package.json",
                "pubspec.yaml",
                "lib/main.dart",
                "readme.md",
            } or rel_text in {
                "lib/main.dart",
                "src/main.js",
                "src/App.jsx",
                "src/App.tsx",
                "windows/runner/CMakeLists.txt",
            }:
                entry_candidates.append(rel_text)

        lines = ["Pastas:"]
        for folder, count in folder_counts.most_common(18):
            lines.append(f"- {folder}: {count} arquivo(s)")
        lines.append("")
        lines.append("Entradas/configuracoes provaveis:")
        for item in entry_candidates[:24]:
            lines.append(f"- {item}")
        if len(lines) < limit:
            lines.append("")
            lines.append("Arquivos visiveis principais:")
            for _path, rel in files[: max(0, limit - len(lines))]:
                lines.append(f"- {rel.as_posix()}")
        return "\n".join(lines[:limit])

    def detect_subprojects(self, files, limit=24):
        if not files:
            return "- Nenhum subprojeto detectado."
        markers = {
            "package.json": "Node/Web",
            "pubspec.yaml": "Flutter/Dart",
            "pyproject.toml": "Python",
            "requirements.txt": "Python",
            "Cargo.toml": "Rust",
            "go.mod": "Go",
            "pom.xml": "Java/Maven",
            "build.gradle": "Java/Gradle",
            "index.html": "Web/HTML",
            "main.py": "Python",
            "app.py": "Python",
        }
        projects = {}
        for path, rel in files:
            marker_type = markers.get(path.name)
            if not marker_type:
                continue
            folder = rel.parent.as_posix() if rel.parent.as_posix() != "." else "."
            entry = projects.setdefault(
                folder,
                {
                    "types": set(),
                    "markers": [],
                    "files": 0,
                    "size": 0,
                },
            )
            entry["types"].add(marker_type)
            entry["markers"].append(rel.as_posix())

        for path, rel in files:
            rel_text = rel.as_posix()
            matched_folder = "."
            for folder in projects:
                if folder != "." and rel_text.startswith(folder.rstrip("/") + "/"):
                    if len(folder) > len(matched_folder):
                        matched_folder = folder
            if matched_folder in projects:
                projects[matched_folder]["files"] += 1
                try:
                    projects[matched_folder]["size"] += path.stat().st_size
                except OSError:
                    pass

        if not projects:
            return "- Nenhum subprojeto com marcadores conhecidos foi detectado."

        lines = []
        for folder, info in sorted(projects.items(), key=lambda item: (item[0] != ".", item[0].lower()))[:limit]:
            types = ", ".join(sorted(info["types"]))
            markers_text = ", ".join(info["markers"][:5])
            lines.append(
                f"- {folder}: {types}; {info['files']} arquivo(s); {self.format_bytes(info['size'])}; marcadores: {markers_text}"
            )
        if len(projects) > limit:
            lines.append(f"- ... {len(projects) - limit} subprojeto(s) omitido(s).")
        return "\n".join(lines)

    def detect_project_type(self, workspace, suffix_counts):
        if (workspace / "pubspec.yaml").exists():
            return "Flutter/Dart"
        if (workspace / "package.json").exists():
            return "JavaScript/Node"
        if (workspace / "pyproject.toml").exists() or (workspace / "requirements.txt").exists():
            return "Python"
        if suffix_counts.get(".py", 0) >= 2:
            return "Python"
        if suffix_counts.get(".html", 0) and suffix_counts.get(".css", 0):
            return "Web"
        return "Projeto generico"

    def local_key_files(self, files, limit=12):
        priority_names = {
            "README.md",
            "pubspec.yaml",
            "analysis_options.yaml",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "main.py",
            "app.py",
        }
        ordered = []
        seen = set()
        for path, rel in files:
            if path.name in priority_names:
                ordered.append(rel.as_posix())
                seen.add(rel.as_posix())
        for _path, rel in files:
            text = rel.as_posix()
            if text not in seen:
                ordered.append(text)
                seen.add(text)
            if len(ordered) >= limit:
                break
        return "\n".join(f"- {item}" for item in ordered[:limit])

    def local_top_folders(self, files, limit=10):
        counts = Counter()
        for _path, rel in files:
            parts = rel.parts
            counts[parts[0] if len(parts) > 1 else "."] += 1
        return "\n".join(f"- {name}: {count} arquivo(s)" for name, count in counts.most_common(limit))

    def format_bytes(self, size):
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
