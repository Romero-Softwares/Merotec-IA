import base64
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import PIL.Image
try:
    from google.genai import Client as GoogleClient
    from google.genai import types
except ModuleNotFoundError:
    GoogleClient = None
    types = None

from modules import config as app_config


class UniversalEngine:
    def __init__(self):
        self.provider = os.getenv("AI_PROVIDER", app_config.AI_PROVIDER).strip().lower()
        self.codex_model_name = os.getenv("CODEX_MODEL_NAME", app_config.CODEX_MODEL_NAME).strip()
        self.codex_reasoning_effort = os.getenv(
            "CODEX_REASONING_EFFORT",
            app_config.CODEX_REASONING_EFFORT,
        ).strip().lower() or "xhigh"
        self.openai_api_key = os.getenv("OPENAI_API_KEY", app_config.OPENAI_API_KEY).strip()
        self.openai_model_name = os.getenv("OPENAI_MODEL_NAME", app_config.OPENAI_MODEL_NAME).strip()
        self.google_api_key = os.getenv("GOOGLE_API_KEY", app_config.GOOGLE_API_KEY).strip()
        self.google_model_name = os.getenv("GOOGLE_MODEL_NAME", app_config.MODEL_NAME).strip()
        self.language = os.getenv("APP_LANGUAGE", app_config.LANGUAGE).strip()

        self.client = None
        self.chat_session = None
        self.active_process = None
        self.cancel_requested = False
        self.model_id = self._resolve_model_id()
        self.latest_rate_limits = None
        self.latest_token_usage = None
        self.latest_quota_problem = ""
        self.latest_quota_updated_at = 0
        self.system_instruction = self._build_system_instruction()
        self.generation_config = self._build_google_generation_config()

        if self.provider == "codex":
            self.codex_executable = self._find_codex_executable()
            self.client = "codex-cli" if self.codex_executable and self._codex_is_logged_in(self.codex_executable) else None
            return

        if self.provider == "openai":
            self.client = "openai-http" if self.openai_api_key else None
            return

        if self.google_api_key and GoogleClient:
            self.client = GoogleClient(api_key=self.google_api_key)
            self.reset_session()

    def _resolve_model_id(self):
        if self.provider == "codex":
            return self.codex_model_name or "gpt-5.5"
        if self.provider == "openai":
            return self.openai_model_name
        if self.provider == "google":
            return self.google_model_name
        return self.codex_model_name or "gpt-5.5"

    def status_text(self):
        if self.provider == "codex":
            if self.client:
                key_state = "logado"
            elif getattr(self, "codex_executable", None):
                key_state = "sem login"
            else:
                key_state = "nao encontrado"
        else:
            key_state = "chave ok" if self.client else "sem chave"
        effort = f" | raciocinio {self.codex_reasoning_effort}" if self.provider == "codex" else ""
        return f"{self.provider.upper()} | {self.model_id}{effort} | {key_state}"

    def quota_status_text(self):
        model = self.model_id or "modelo atual"
        if self.provider == "codex" and self.codex_reasoning_effort:
            model = f"{model}/{self.codex_reasoning_effort}"
        prefix = f"{self.provider.upper()} {model}"

        if self.latest_quota_problem:
            return f"{prefix} | {self.latest_quota_problem}"

        rate_text = self._format_rate_limit_status(self.latest_rate_limits)
        token_text = self._format_token_usage_status(self.latest_token_usage)
        if rate_text and token_text:
            return f"{prefix} | {rate_text} | {token_text}"
        if rate_text:
            return f"{prefix} | {rate_text}"
        if token_text:
            return f"{prefix} | {token_text}"
        return f"{prefix} | uso aguardando"

    def _remember_rate_limits(self, payload):
        if not isinstance(payload, dict):
            return
        rate_limits = payload.get("rateLimits") if "rateLimits" in payload else payload
        by_limit = payload.get("rateLimitsByLimitId")
        if isinstance(by_limit, dict):
            rate_limits = by_limit.get("codex") or rate_limits or next(iter(by_limit.values()), None)
        if isinstance(rate_limits, dict):
            self.latest_rate_limits = rate_limits
            self.latest_quota_updated_at = time.time()
            reached = str(rate_limits.get("rateLimitReachedType") or "").strip()
            credits = rate_limits.get("credits") or {}
            if reached:
                self.latest_quota_problem = self._human_rate_limit_reached(reached)
            elif isinstance(credits, dict) and credits.get("hasCredits") is False and not credits.get("unlimited"):
                self.latest_quota_problem = "creditos esgotados"
            else:
                self.latest_quota_problem = ""

    def _remember_token_usage(self, payload):
        if not isinstance(payload, dict):
            return
        token_usage = payload.get("tokenUsage") if "tokenUsage" in payload else payload
        if isinstance(token_usage, dict):
            self.latest_token_usage = token_usage
            self.latest_quota_updated_at = time.time()

    def _remember_openai_usage(self, usage):
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("input_tokens", usage.get("inputTokens", 0)) or 0
        output_tokens = usage.get("output_tokens", usage.get("outputTokens", 0)) or 0
        total_tokens = usage.get("total_tokens", usage.get("totalTokens"))
        if total_tokens is None:
            try:
                total_tokens = int(input_tokens) + int(output_tokens)
            except (TypeError, ValueError):
                total_tokens = 0
        self._remember_token_usage(
            {
                "last": {
                    "cachedInputTokens": usage.get("cached_input_tokens", usage.get("cachedInputTokens", 0)) or 0,
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "reasoningOutputTokens": usage.get(
                        "reasoning_output_tokens",
                        usage.get("reasoningOutputTokens", 0),
                    ) or 0,
                    "totalTokens": total_tokens,
                },
                "total": {
                    "cachedInputTokens": usage.get("cached_input_tokens", usage.get("cachedInputTokens", 0)) or 0,
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "reasoningOutputTokens": usage.get(
                        "reasoning_output_tokens",
                        usage.get("reasoningOutputTokens", 0),
                    ) or 0,
                    "totalTokens": total_tokens,
                },
            }
        )

    def _remember_quota_error(self, payload):
        if not isinstance(payload, dict):
            return
        error = payload.get("error") or payload
        if not isinstance(error, dict):
            return
        text = " ".join(
            str(value)
            for value in (
                error.get("message"),
                error.get("additionalDetails"),
                error.get("codexErrorInfo"),
            )
            if value
        ).lower()
        if "usagelimitexceeded" in text or "usage limit" in text or "rate limit" in text:
            self.latest_quota_problem = "sem cota/limite atingido"
            self.latest_quota_updated_at = time.time()

    def _remember_app_server_quota_message(self, method, params):
        lower_method = (method or "").lower()
        if "ratelimits" in lower_method:
            self._remember_rate_limits(params)
        if "tokenusage" in lower_method or "token_usage" in lower_method:
            self._remember_token_usage(params)
        if lower_method in {"error", "warning"} or lower_method.endswith("/error"):
            self._remember_quota_error(params)

    def _human_rate_limit_reached(self, value):
        labels = {
            "rate_limit_reached": "limite de taxa atingido",
            "workspace_owner_credits_depleted": "creditos do workspace esgotados",
            "workspace_member_credits_depleted": "creditos do membro esgotados",
            "workspace_owner_usage_limit_reached": "limite do workspace atingido",
            "workspace_member_usage_limit_reached": "limite do membro atingido",
        }
        return labels.get(value, value.replace("_", " "))

    def _format_rate_limit_status(self, rate_limits):
        if not isinstance(rate_limits, dict):
            return ""
        pieces = []
        plan = rate_limits.get("planType")
        if plan and plan != "unknown":
            pieces.append(str(plan))
        limit_name = rate_limits.get("limitName") or rate_limits.get("limitId")
        if limit_name:
            pieces.append(str(limit_name))
        for label, key in (("janela", "primary"), ("extra", "secondary")):
            window = rate_limits.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if used is None:
                continue
            window_text = f"{label} {used}%"
            reset_text = self._format_reset_time(window.get("resetsAt"))
            if reset_text:
                window_text += f" reset {reset_text}"
            pieces.append(window_text)
        credits = rate_limits.get("credits")
        if isinstance(credits, dict):
            if credits.get("unlimited"):
                pieces.append("creditos ilimitados")
            elif credits.get("balance"):
                pieces.append(f"creditos {credits.get('balance')}")
        return " | ".join(pieces)

    def _format_token_usage_status(self, token_usage):
        if not isinstance(token_usage, dict):
            return ""
        total = token_usage.get("total") or {}
        last = token_usage.get("last") or {}
        total_tokens = total.get("totalTokens") if isinstance(total, dict) else None
        last_tokens = last.get("totalTokens") if isinstance(last, dict) else None
        context_window = token_usage.get("modelContextWindow")
        if total_tokens is not None and context_window:
            return f"ctx {self._compact_number(total_tokens)}/{self._compact_number(context_window)}"
        if total_tokens is not None:
            return f"tokens {self._compact_number(total_tokens)}"
        if last_tokens is not None:
            return f"ultima {self._compact_number(last_tokens)} tokens"
        return ""

    def _format_reset_time(self, value):
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return ""
        if timestamp <= 0:
            return ""
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        seconds = max(0, int(timestamp - time.time()))
        if seconds <= 0:
            return "agora"
        minutes = max(1, round(seconds / 60))
        if minutes < 60:
            return f"{minutes}min"
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h{minutes:02d}"

    def _compact_number(self, value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return str(value)
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}k"
        return str(number)

    def _find_codex_executable(self):
        candidates = []
        roots = [
            Path(os.getenv("ProgramFiles", "")) / "WindowsApps",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
        ]
        patterns = [
            "OpenAI.Codex_*\\app\\resources\\codex.exe",
            "OpenAI.Codex_*\\app\\resources\\codex",
            "codex.exe",
        ]
        for root in roots:
            if not root.exists():
                continue
            for pattern in patterns:
                try:
                    for candidate in sorted(root.glob(pattern), reverse=True):
                        if candidate.exists():
                            candidates.append(str(candidate))
                except OSError:
                    continue

        for executable in (shutil.which("codex.exe"), shutil.which("codex")):
            if executable:
                candidates.append(executable)

        for candidate in dict.fromkeys(candidates):
            if self._can_run_codex(candidate):
                return candidate
        return None

    def _can_run_codex(self, executable):
        try:
            process = subprocess.Popen(
                [executable, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=5)
            return process.returncode == 0 and "codex" in (output or "").lower()
        except Exception:
            return False

    def _codex_is_logged_in(self, executable=None):
        executable = executable or self._find_codex_executable()
        if not executable:
            return False
        try:
            process = subprocess.Popen(
                [executable, "login", "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output, _ = process.communicate(timeout=12)
            lower_output = (output or "").lower()
            return process.returncode == 0 and "not logged in" not in lower_output
        except Exception:
            return False

    def _build_system_instruction(self):
        return f"""Voce e um agente autonomo de engenharia de software integrado a IDE Merotec AI.
Idioma preferido: {self.language}.

Voce pode solicitar acoes da IDE usando tags especiais:
[READ: caminho/arquivo.py] para ler arquivo antes de alterar.
[READ: caminho/arquivo.py | linhas 120-260] para ler um intervalo especifico de arquivo grande.
[WRITE: caminho/arquivo.py] ... [/WRITE] para criar ou sobrescrever arquivo com backup automatico.
[REPLACE: caminho/arquivo.py] [OLD] trecho atual exato [/OLD] [NEW] trecho novo [/NEW] [/REPLACE] para trocar um trecho pequeno com backup automatico.
[SEARCH_TEXT: padrao | caminho/arquivo.py] para a IDE buscar termos ou regex em arquivo sem usar terminal.
[WEB_SEARCH: consulta objetiva] para a IDE buscar na internet quando a solucao depender de documentacao externa, fatos atuais ou erro desconhecido.
[SCAN_TEXT: caminho/arquivo.py] para a IDE localizar caracteres corrompidos/mojibake e problemas de texto sem usar terminal.
[FIX_MOJIBAKE: caminho/arquivo.py] para a IDE corrigir mojibake comum com backup automatico.
Para rodar terminal, envie uma tag EXECUTE ja preenchida, por exemplo [EXECUTE: python -m unittest].
Para administrador no Windows, envie uma tag EXECUTE_ADMIN ja preenchida, por exemplo [EXECUTE_ADMIN: whoami /groups].
[OPEN_URL: http://127.0.0.1:porta/] para abrir uma URL local validada.
[SCREENSHOT: tela] para capturar a tela atual e devolver a imagem para analise.
[HUMAN_TEST: auto] para a IDE executar/abrir o app ou jogo, esperar a tela, capturar print real e devolver para analise visual.
[UNDO: caminho/arquivo.py] para restaurar o backup .bak.

Regras:
- Modo Codex: comporte-se como um agente de engenharia integrado, nao como chatbot comum.
- Use raciocinio altissimo: antes de responder, escolha o proximo passo que realmente muda, executa, valida ou conclui.
- Ciclo obrigatorio: entender a missao, escolher poucos arquivos relevantes, aplicar alteracao quando pedida, validar com comando/print quando possivel e fechar com resumo objetivo.
- Se a pergunta for simples e nao exigir projeto, responda diretamente sem tags.
- Se a missao for analise/planejamento, entregue diagnostico completo em texto; nao transforme analise em execucao ou edicao.
- Se a missao for implementacao/correcao, nao pare em "vou fazer"; use [READ], [REPLACE], [WRITE] e uma tag EXECUTE com comando real ate haver resultado verificavel.
- Se for usar uma tag, responda com a tag diretamente. Nao escreva "vou", "irei" ou "preciso" antes da tag.
- Texto de intencao sem acao sera ignorado pela IDE. Acao real ou conclusao final sao as unicas saidas validas.
- Nunca diga que corrigiu, aplicou, alterou, rodou, testou ou validou sem enviar a tag real que faz isso.
- Correcao so conta com [REPLACE], [WRITE], [FIX_MOJIBAKE] ou [UNDO]; validacao so conta com uma tag EXECUTE/EXECUTE_ADMIN ja preenchida, [OPEN_URL], [SCREENSHOT] ou [HUMAN_TEST].
- Para projeto grande, use o mapa do workspace, arquivos-chave e buscas pontuais. Nao tente ler tudo em sequencia.
- Depois de 2 ou 3 leituras estrategicas, tome decisao: editar, testar, abrir, capturar print ou concluir.
- Se a IDE avisar que substituiu leitura em massa por mapa do projeto, use o mapa e entregue resultado; nao peca nova lista de arquivos.
- Preserve o trabalho existente: prefira patches pequenos; nao recrie um projeto funcional se o usuario pediu corrigir uma parte.
- Ao corrigir bug, busque a causa no arquivo/camada provavel antes de alterar arquivos aleatorios.
- Ao testar, leia a saida, corrija a causa e teste de novo quando fizer sentido.
- Ao terminar uma missao, diga o que mudou, o que foi verificado e qualquer risco restante.
- Trabalhe com raciocinio alto e autonomia de agente senior: entenda a missao, leia o que faltar, altere, execute e corrija sem pedir o objetivo de novo.
- Nao revele raciocinio interno detalhado; mostre apenas a decisao, a acao e o resultado.
- Use apenas caminhos relativos ao workspace, como `app.py`, `src/main.py` ou `style.css`.
- Nunca use caminhos absolutos como `C:/...`.
- Use tags da IDE ou ferramentas diretas do app-server; escolha o caminho real mais confiavel para concluir a tarefa.
- Terminal e app-server podem ser usados para inspecao, validacao e comandos reais dentro do workspace.
- Para edicoes pequenas, prefira [WRITE]/[REPLACE]; se usar ferramenta direta do app-server, mantenha tudo dentro do workspace e relate o que mudou.
- Para alterar um trecho pequeno de arquivo grande, prefira [REPLACE] com o trecho OLD exatamente como foi lido pela IDE.
- Para procurar caracteres corrompidos, mojibake ou texto quebrado, use [SCAN_TEXT] ou uma inspecao direta equivalente.
- Para verificar se existe uma funcao, recurso, variavel, termo ou logica no arquivo, use [SEARCH_TEXT: padrao | arquivo] ou busca direta equivalente.
- Depois de receber resultado de SEARCH_TEXT para uma pergunta simples de verificacao, responda a conclusao; nao faca novas buscas parecidas.
- Para resolver problema que dependa de informacao atual, documentacao externa, erro desconhecido ou comportamento de biblioteca/servico que pode ter mudado, use [WEB_SEARCH: consulta objetiva] antes de concluir.
- Depois de receber resultado de WEB_SEARCH, use as fontes encontradas para decidir a proxima acao; nao repita a mesma busca sem motivo.
- Quando a varredura indicar mojibake comum, use [FIX_MOJIBAKE] antes de validar.
- Para build, run, testes, iniciar servidor ou abrir app sem necessidade visual, responda com uma tag EXECUTE contendo o comando real.
- Para comandos que realmente exigem administrador no Windows, responda com uma tag EXECUTE_ADMIN contendo o comando real. Nao escreva "como administrador" dentro de [EXECUTE].
- Nunca use reticencias, "comando", "comando real", texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real.
- Nunca copie literalmente `comando concreto` nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto.
- Nunca chame terminal, ferramenta de shell ou app-server com comando `...`, `comando`, `como administrador`, `--admin` ou outro placeholder; se nao houver comando real, entregue uma conclusao final.
- Para testar como usuario, validar tela, jogo, layout, print, fluxo visual ou "teste real", responda com [HUMAN_TEST: auto] em vez de ficar lendo arquivos.
- Para apps Flutter, `flutter run -d windows` ja faz build antes de executar no Windows.
- Para comandos que podem ficar rodando, como `flutter run`, `npm run dev` ou servidores locais, use uma tag EXECUTE com o comando real e finalize sua resposta; a IDE mantem o terminal aberto.
- Se um comando falhar, nao repita o mesmo comando antes de aplicar [WRITE] em pelo menos um arquivo suspeito ou ler um arquivo novo que explique a falha.
- Antes de alterar arquivo que voce ainda nao leu, prefira ler o trecho relevante por [READ] ou ferramenta direta equivalente.
- Se a IDE informar que o arquivo e grande, use o indice recebido e peca intervalos com [READ: arquivo | linhas inicio-fim] ate ter contexto suficiente.
- Evite reescrever um arquivo grande inteiro usando apenas o resumo; primeiro leia os intervalos exatos quando isso reduzir risco.
- Se a IDE informar "Leitura bloqueada para evitar ciclo infinito", pare de pedir READ desse arquivo e avance com [WRITE], uma tag EXECUTE com comando real ou uma conclusao objetiva.
- Para arquivos grandes em uma unica pagina, como HTML com CSS/JS embutidos, apos mapear cabecalho, estilos, estado principal e loop/renderizacao, pare de ler e execute a alteracao pedida.
- Prefira alteracoes pequenas, claras e verificaveis.
- Explique o resultado em portugues direto, sem enrolar.
- Quando executar algo, analise a saida e continue somente se necessario.
- Quando o usuario pedir para construir, reconstruir, corrigir ou alterar, nao pergunte "qual o proximo passo"; execute a acao.
- Quando receber contexto com "MISSAO ATIVA DA IA" ou "MISSAO ORIGINAL", trate essa missao como o objetivo principal ate concluir.
- Quando receber "DIAGNOSTICO DE FALHA GERADO PELA IDE", siga a camada provavel, leia/altere os arquivos suspeitos e nao repita o mesmo comando antes de corrigir a causa indicada.
- Em erros de build, identifique a camada antes de agir: Dart, Flutter dependencias, Android/Gradle, Windows CMake, C++ compile ou linker.
- Se a falha for Windows CMake/C++/linker, nao altere `lib/main.dart` nem `pubspec.yaml` sem evidencia direta.
- Para mudar arquivos, responda com tags [WRITE] ou [REPLACE] completas, sempre fechando as tags.
- Se estiver criando um app novo, escreva os arquivos diretamente; nao peca confirmacao.
- Seja objetivo. Evite introducao longa.
"""

    def _build_google_generation_config(self):
        if not types:
            return None
        return types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            temperature=0.1,
            max_output_tokens=8192,
        )

    def _message_payload(self, prompt, code_context=None):
        text_content = f"Instrucao do usuario/sistema: {prompt}"
        if code_context:
            text_content += f"\n\n--- CONTEXTO PARA ANALISAR ---\n{code_context}"
        return text_content

    def _openai_input(self, prompt, code_context=None, image_path=None):
        content = [{"type": "input_text", "text": self._message_payload(prompt, code_context)}]

        if image_path and os.path.exists(image_path):
            path = Path(image_path)
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"})

        return [{"role": "user", "content": content}]

    def generate_stream(self, prompt, code_context=None):
        if self.provider in {"codex", "openai"} or not self.chat_session:
            return None
        try:
            return self.chat_session.send_message_stream(self._message_payload(prompt, code_context))
        except Exception:
            return None

    def generate_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
    ):
        self.cancel_requested = False
        if self.provider == "codex":
            return self._generate_codex_solution(
                prompt,
                image_path,
                code_context,
                stream_callback=stream_callback,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
        if self.provider == "openai":
            return self._generate_openai_solution(prompt, image_path, code_context)
        return self._generate_google_solution(prompt, image_path, code_context)

    def _generate_codex_solution(
        self,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        workspace_path=None,
        approval_callback=None,
    ):
        executable = self._find_codex_executable()
        if not executable:
            return (
                "Codex nao foi encontrado no Windows. A IDE tentara abrir o instalador automaticamente. "
                "Depois de instalar, faca login no Codex e tente novamente."
            )
        if not self._codex_is_logged_in(executable):
            return (
                "O Codex que a IDE chama ainda nao esta logado no Windows. "
                "Clique em `Entrar Codex`, conclua o login do Codex CLI e tente novamente."
            )

        for effort in self._codex_reasoning_efforts():
            app_server_response = self._generate_codex_app_server_solution(
                executable,
                prompt,
                image_path=image_path,
                code_context=code_context,
                stream_callback=stream_callback,
                reasoning_effort=effort,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
            if app_server_response:
                if self._is_capacity_message(app_server_response):
                    fallback_response = self._try_codex_fallback_models(
                        executable,
                        prompt,
                        image_path=image_path,
                        code_context=code_context,
                        stream_callback=stream_callback,
                        reasoning_effort=effort,
                        workspace_path=workspace_path,
                        approval_callback=approval_callback,
                    )
                    if fallback_response:
                        return fallback_response
                if self._is_codex_progress_timeout(app_server_response):
                    if stream_callback:
                        stream_callback(
                            "\nCodex app-server ficou silencioso; continuando pelo executor direto...\n"
                        )
                    break
                return app_server_response

        last_exec_response = ""
        for effort in self._codex_reasoning_efforts():
            last_exec_response = self._generate_codex_exec_solution(
                executable,
                prompt,
                image_path,
                code_context,
                stream_callback=stream_callback,
                reasoning_effort=effort,
                workspace_path=workspace_path,
            )
            if last_exec_response and not self._is_reasoning_effort_error(last_exec_response):
                return last_exec_response
        return last_exec_response or "Codex nao conseguiu iniciar a tarefa."

    def _codex_reasoning_efforts(self):
        configured = (self.codex_reasoning_effort or "xhigh").strip().lower()
        efforts = []
        for effort in (configured, "xhigh", "high"):
            if effort and effort not in efforts:
                efforts.append(effort)
        return efforts

    def _try_codex_fallback_models(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
        approval_callback=None,
    ):
        fallback_models = ["gpt-5.4-mini", "gpt-5.3-codex-spark"]
        requested = self.codex_model_name.strip()
        for model in fallback_models:
            if requested and requested == model:
                continue
            response = self._generate_codex_app_server_solution(
                executable,
                prompt,
                image_path=image_path,
                code_context=code_context,
                model_override=model,
                stream_callback=stream_callback,
                reasoning_effort=reasoning_effort,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
            )
            if response and not self._is_capacity_message(response) and not self._is_codex_error_message(response):
                return f"[Codex alternativo: {model}]\n\n{response}"
        return None

    def _is_capacity_message(self, text):
        lower = (text or "").lower()
        return "alta demanda" in lower or self._is_codex_capacity_error(lower)

    def _is_codex_progress_timeout(self, text):
        lower = (text or "").lower()
        return "sem enviar progresso" in lower or "ficou sem enviar progresso" in lower

    def _positive_int_env(self, name, default, minimum=1, maximum=None):
        try:
            value = int(os.getenv(name, "").strip())
        except (TypeError, ValueError):
            value = int(default)
        value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    def _is_codex_error_message(self, text):
        lower = (text or "").lower()
        error_markers = [
            "codex app-server retornou erro",
            "codex retornou erro",
            "not available",
            "not found",
            "unknown model",
            "invalid model",
            "nao esta logado",
        ]
        return any(marker in lower for marker in error_markers)

    def _is_reasoning_effort_error(self, text):
        lower = (text or "").lower()
        return (
            "model_reasoning_effort" in lower
            and any(marker in lower for marker in ("invalid", "unknown", "unexpected", "supported", "value"))
        )

    def _generate_codex_app_server_solution(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        model_override=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
        approval_callback=None,
    ):
        workspace = Path(workspace_path).resolve() if workspace_path else Path.cwd()
        selected_model = model_override or self.codex_model_name or "gpt-5.5"
        selected_effort = (reasoning_effort or self.codex_reasoning_effort or "xhigh").strip().lower()
        approval_policy = (
            os.getenv("MEROTEC_CODEX_APP_SERVER_APPROVAL_POLICY", "on-request").strip()
            or "on-request"
        )
        if approval_policy not in {"on-request", "on-failure", "never", "untrusted"}:
            approval_policy = "on-request"
        prompt_text = (
            "Voce esta respondendo dentro da Merotec IA IDE via Codex app-server.\n"
            f"Use raciocinio altissimo nesta tarefa: effort={selected_effort}.\n"
            "Use o workspace atual como projeto ativo.\n"
            "Responda com resultado final ou acao real. Nao escreva promessas como 'vou fazer' antes de executar.\n"
            "Se for usar uma tag da IDE, envie a tag diretamente, sem texto narrando intencao antes dela.\n"
            "Use tags da IDE ou ferramentas diretas do app-server; escolha o caminho real mais confiavel para concluir a tarefa.\n"
            "Use [READ], [WRITE], [REPLACE], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], [FIX_MOJIBAKE], tags EXECUTE/EXECUTE_ADMIN ja preenchidas, [OPEN_URL], [SCREENSHOT], [HUMAN_TEST] e [UNDO].\n"
            "Se as tags da IDE estiverem limitando a conclusao da tarefa e o app-server disponibilizar ferramentas diretas, aja diretamente no workspace como Codex, mantendo as mudancas dentro da pasta do projeto e relatando o que fez.\n"
            "Quando a tarefa for correcao/alteracao, use [READ], [SEARCH_TEXT], [WEB_SEARCH], [SCAN_TEXT], [FIX_MOJIBAKE], [REPLACE], [WRITE] ou ferramentas diretas equivalentes. "
            "Quando a solucao depender de documentacao externa, informacao atual ou erro desconhecido, use [WEB_SEARCH: consulta objetiva] para a IDE buscar na internet.\n"
            "Use uma tag EXECUTE com comando real para validar depois da correcao ou quando o pedido for apenas rodar/iniciar; use EXECUTE_ADMIN com comando real somente quando precisar UAC/administrador no Windows; use [HUMAN_TEST: auto] quando precisar abrir, capturar print e avaliar a tela como usuario.\n"
            "Nunca use reticencias, 'comando', 'comando real', texto entre sinais de menor/maior ou qualquer texto demonstrativo como se fosse comando real.\n"
            "Nunca copie literalmente 'comando concreto' nas tags [EXECUTE] ou [EXECUTE_ADMIN]; se ainda nao houver comando real, entregue uma conclusao em texto.\n"
            "Nunca chame terminal, ferramenta de shell ou app-server com comando `...`, `comando`, `como administrador`, `--admin` ou outro placeholder; se nao houver comando real, entregue uma conclusao final.\n"
            "Nunca diga que corrigiu, aplicou, alterou, rodou, testou ou validou sem uma acao real: "
            "[REPLACE]/[WRITE] para mudar arquivo, EXECUTE com comando real para rodar, EXECUTE_ADMIN com comando real para pedir permissao de administrador, [HUMAN_TEST] quando precisar testar visualmente como usuario, [OPEN_URL]/[SCREENSHOT] quando a tela ja estiver aberta.\n\n"
            f"{self._message_payload(prompt, code_context)}"
        )

        try:
            process = subprocess.Popen(
                [
                    executable,
                    "app-server",
                    "-c",
                    'model_provider="openai"',
                    "-c",
                    f'model="{selected_model}"',
                    "-c",
                    f'model_reasoning_effort="{selected_effort}"',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(workspace),
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.active_process = process
        except Exception:
            return None

        messages = queue.Queue()

        def reader():
            try:
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.put(json.loads(line))
                    except json.JSONDecodeError:
                        messages.put({"method": "rawOutput", "params": {"text": line}})
            finally:
                messages.put({"method": "processClosed", "params": {}})

        threading.Thread(target=reader, daemon=True).start()

        next_id = 0

        def send(method, params=None, request_id=None):
            payload = {"method": method}
            if params is not None:
                payload["params"] = params
            if request_id is not None:
                payload["id"] = request_id
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def respond(request_id, result):
            payload = {"id": request_id, "result": result}
            process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def ask_approval(method, params):
            if approval_callback is None:
                return True
            try:
                return bool(approval_callback(method, params or {}, str(workspace)))
            except Exception:
                return False

        def handle_server_request(message):
            request_id = message.get("id")
            method = message.get("method", "")
            params = message.get("params") or {}
            if request_id is None or not method:
                return False
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                approved = False if self._app_server_message_has_placeholder_command(method, params) else ask_approval(method, params)
                decision = "acceptForSession" if approved else "reject"
                respond(request_id, {"decision": decision})
                return True
            if method in {"execCommandApproval", "applyPatchApproval"}:
                approved = False if self._app_server_message_has_placeholder_command(method, params) else ask_approval(method, params)
                decision = "approved_for_session" if approved else "denied"
                respond(request_id, {"decision": decision})
                return True
            if method == "item/permissions/requestApproval":
                if not ask_approval(method, params):
                    respond(
                        request_id,
                        {
                            "permissions": {
                                "fileSystem": {"entries": []},
                                "network": {"enabled": False},
                            },
                            "scope": "turn",
                            "strictAutoReview": True,
                        },
                    )
                    return True
                respond(
                    request_id,
                    {
                        "permissions": {
                            "fileSystem": {
                                "entries": [
                                    {
                                        "access": "write",
                                        "path": {"type": "path", "path": str(workspace)},
                                    }
                                ]
                            },
                            "network": {"enabled": True},
                        },
                        "scope": "turn",
                        "strictAutoReview": False,
                    },
                )
                return True
            return False

        def wait_for_response(request_id, timeout=20):
            deadline = time.time() + timeout
            buffered_notifications = []
            while time.time() < deadline:
                try:
                    message = messages.get(timeout=0.5)
                except queue.Empty:
                    continue
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message", str(message["error"])))
                    return message.get("result") or {}
                if handle_server_request(message):
                    continue
                buffered_notifications.append(message)
            raise TimeoutError("Codex app-server nao respondeu a tempo.")

        try:
            send(
                "initialize",
                {
                    "clientInfo": {
                        "name": "merotec_ide",
                        "title": "Merotec IA IDE",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                next_id,
            )
            wait_for_response(next_id, timeout=20)
            next_id += 1
            send("initialized", {})
            try:
                send("account/rateLimits/read", {}, next_id)
                rate_limit_result = wait_for_response(next_id, timeout=8)
                self._remember_rate_limits(rate_limit_result)
                next_id += 1
            except Exception:
                next_id += 1

            thread_params = {
                "cwd": str(workspace),
                "developerInstructions": self.system_instruction,
                "sandbox": "workspace-write",
                "approvalsReviewer": "user",
                "personality": "friendly",
                "threadSource": "user",
                "ephemeral": True,
                "modelReasoningEffort": selected_effort,
                "approvalPolicy": approval_policy,
            }
            if selected_model:
                thread_params["model"] = selected_model

            send("thread/start", thread_params, next_id)
            thread_result = wait_for_response(next_id, timeout=30)
            next_id += 1
            thread_id = (thread_result.get("thread") or {}).get("id")
            if not thread_id:
                return None

            input_items = [{"type": "text", "text": prompt_text}]
            if image_path and os.path.exists(image_path):
                input_items.append({"type": "localImage", "path": str(Path(image_path).resolve())})

            turn_params = {
                "threadId": thread_id,
                "input": input_items,
                "cwd": str(workspace),
                "approvalPolicy": approval_policy,
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": True,
                    "writableRoots": [str(workspace)],
                },
                "summary": "concise",
                "personality": "friendly",
                "modelReasoningEffort": selected_effort,
            }
            if selected_model:
                turn_params["model"] = selected_model

            send("turn/start", turn_params, next_id)
            turn_response_id = next_id
            next_id += 1

            chunks = []
            final_from_items = ""
            last_error = ""
            completed = False
            task_timeout = self._positive_int_env(
                "MEROTEC_CODEX_TASK_TIMEOUT_SECONDS",
                3600,
                minimum=300,
                maximum=14400,
            )
            idle_timeout = self._positive_int_env(
                "MEROTEC_CODEX_APP_SERVER_IDLE_TIMEOUT_SECONDS",
                900,
                minimum=120,
                maximum=7200,
            )
            deadline = time.time() + task_timeout
            last_activity = time.time()

            while time.time() < deadline:
                if self.cancel_requested:
                    last_error = "Tarefa cancelada pelo usuario."
                    break
                try:
                    message = messages.get(timeout=1)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    if time.time() - last_activity > idle_timeout:
                        last_error = (
                            f"Codex ficou sem enviar progresso por mais de {idle_timeout} segundos. "
                            "A IDE vai tentar continuar pelo executor direto do Codex."
                        )
                        break
                    continue
                last_activity = time.time()

                if message.get("id") == turn_response_id and "error" in message:
                    last_error = message["error"].get("message", str(message["error"]))
                    break

                method = message.get("method", "")
                params = message.get("params") or {}
                self._remember_app_server_quota_message(method, params)

                if handle_server_request(message):
                    continue

                if self._app_server_message_has_placeholder_command(method, params):
                    last_error = (
                        "Codex app-server tentou executar um comando placeholder. "
                        "A IDE bloqueou a rodada para nao repetir reticencias no terminal."
                    )
                    break

                if self._app_server_output_is_placeholder_error(method, params):
                    last_error = (
                        "O app-server recebeu um placeholder como comando. "
                        "A IDE interrompeu a repeticao; use um comando real ou conclua em texto."
                    )
                    break

                if method.endswith("agentMessage/delta") or method == "agentMessageDelta":
                    delta = params.get("delta", "")
                    chunks.append(delta)
                    if delta and stream_callback:
                        stream_callback(delta)
                    continue

                progress = self._extract_app_server_progress(method, params)
                if progress and stream_callback:
                    stream_callback(progress)
                    continue

                if method in {"error", "warning", "rawOutput"}:
                    last_error = params.get("message") or params.get("text") or last_error
                    continue

                if method.endswith("turn/completed") or method == "turnCompleted":
                    completed = True
                    turn = params.get("turn") or {}
                    if turn.get("status") == "failed":
                        error = turn.get("error") or {}
                        self._remember_quota_error({"error": error})
                        last_error = error.get("message") or str(error)
                    else:
                        final_from_items = self._extract_app_server_final_message(turn)
                    break

                if method == "processClosed":
                    break

            final_message = "".join(chunks).strip() or final_from_items.strip()
            if last_error and self._is_codex_progress_timeout(last_error) and not completed:
                return self._format_codex_app_server_error(last_error)
            if final_message:
                return final_message
            if last_error:
                return self._format_codex_app_server_error(last_error)
            if completed:
                return "Codex terminou a tarefa sem mensagem final."
            return None
        except Exception:
            return None
        finally:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            if self.active_process is process:
                self.active_process = None

    def _extract_app_server_progress(self, method, params):
        lower_method = (method or "").lower()
        progress_methods = ("command", "exec", "patch", "filechange")
        if not any(marker in lower_method for marker in progress_methods):
            return ""

        text = (
            params.get("delta")
            or params.get("text")
            or params.get("output")
            or params.get("message")
            or ""
        )
        if text:
            return text

        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if command and any(marker in lower_method for marker in ("start", "begin", "created")):
            return f"\nExecutando: {command}\n"

        status = params.get("status")
        if status:
            return f"\nStatus: {status}\n"

        return ""

    def _app_server_message_has_placeholder_command(self, method, params):
        lower_method = (method or "").lower()
        if not any(marker in lower_method for marker in ("command", "exec", "shell")):
            return False
        command = self._extract_app_server_command_text(params)
        return bool(command and self._is_placeholder_command_text(command))

    def _app_server_output_is_placeholder_error(self, method, params):
        lower_method = (method or "").lower()
        if not any(marker in lower_method for marker in ("raw", "output", "error", "warning", "command", "exec")):
            return False
        text = (
            params.get("output")
            or params.get("text")
            or params.get("message")
            or params.get("delta")
            or ""
        )
        normalized = str(text or "").lower()
        if not normalized:
            return False
        return bool(
            re.search(r"'\s*(?:\.{3}|\u2026|`+)\s*'.*(?:reconhecido|recognized)", normalized)
            or re.search(r"'\s*(?:comando|command)(?:\s+(?:real|concreto|concrete|here))?\s*'.*(?:reconhecido|recognized)", normalized)
        )

    def _extract_app_server_command_text(self, value, depth=0):
        if depth > 5:
            return ""
        if isinstance(value, dict):
            executable = self._first_present_app_server_value(
                value,
                ("program", "executable", "filePath", "file_path", "binary", "shell"),
            )
            arguments = self._first_present_app_server_value(
                value,
                ("argv", "args", "arguments", "argList", "argumentList", "argument_list"),
            )
            if executable is not None and arguments is not None:
                parts = [
                    self._compact_app_server_command_value(executable),
                    self._compact_app_server_command_value(arguments),
                ]
                return " ".join(part for part in parts if part).strip()

            preferred_keys = (
                "command",
                "commandLine",
                "command_line",
                "cmdLine",
                "cmdline",
                "cmd",
                "shellCommand",
                "shell_command",
                "script",
                "argv",
                "args",
                "arguments",
                "argList",
                "argumentList",
                "argument_list",
            )
            for key in preferred_keys:
                if key not in value:
                    continue
                nested = value[key]
                if isinstance(nested, dict):
                    found = self._extract_app_server_command_text(nested, depth + 1)
                    if found:
                        return found
                return self._compact_app_server_command_value(nested)
            for nested in value.values():
                found = self._extract_app_server_command_text(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            return self._compact_app_server_command_value(value)
        return ""

    def _first_present_app_server_value(self, values, keys):
        for key in keys:
            if key in values and values[key] not in (None, "", [], False):
                return values[key]
        return None

    def _compact_app_server_command_value(self, value):
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def _is_placeholder_command_text(self, command):
        raw = str(command or "").strip().strip("`\"'").strip()
        lowered = raw.lower().strip()
        if not lowered:
            return True

        placeholders = {
            "...",
            "\u2026",
            "?",
            "`",
            "``",
            "```",
            "e termine com",
            "termine com",
            "comece com",
            "comece com e termine com",
            "start with",
            "start with and end with",
            "end with",
            "comando",
            "comando real",
            "comando concreto",
            "comando completo",
            "comando aqui",
            "comando concreto aqui",
            "command",
            "command here",
            "concrete command",
            "complete command",
            "your command",
            "your command here",
            "como administrador",
            "run as administrator",
            "--admin",
            "/admin",
        }
        lowered_without_ticks = lowered.replace("`", "").strip()
        core = re.sub(r"[\s.<>\[\]{}()_`\-./\\:;|&=\u2026?]+", "", lowered_without_ticks)
        placeholder_cores = {
            re.sub(r"[\s.<>\[\]{}()_`\-./\\:;|&=\u2026?]+", "", item)
            for item in placeholders
        }
        if lowered in placeholders or lowered_without_ticks in placeholders or core in placeholder_cores:
            return True

        shell_payload_patterns = (
            r"^(?:/[ck]\s+)(.+)$",
            r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)(.+)$",
            r"^(?:(?:powershell|pwsh)(?:\.exe)?\b.*?(?:-command|-c)\s+)(.+)$",
        )
        for pattern in shell_payload_patterns:
            match = re.match(pattern, lowered)
            if not match:
                continue
            payload = match.group(1).strip().strip("`\"'").strip()
            if payload and payload != lowered and self._is_placeholder_command_text(payload):
                return True

        patterns = (
            r"^(?:o\s+|um\s+)?comando(?:\s+(?:real|completo|concreto|aqui|do\s+projeto|preenchido))+$",
            r"^(?:seu|your)\s+comando(?:\s+aqui)?$",
            r"^<[^>]*(?:comando|command)[^>]*>$",
            r"^(?:e\s+)?termine\s+com$",
            r"^(?:comece|comeca)\s+com(?:\s+e\s+termine\s+com)?$",
            r"^start\s+with(?:\s+and\s+end\s+with)?$",
            r"^end\s+with$",
            r"^(?:cmd(?:\.exe)?\s+)?/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
            r"^cmd(?:\.exe)?\s+/[ck]\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
            r"^(?:powershell|pwsh)(?:\.exe)?\s+.*(?:-command|-c)\s+['\"]?(?:\.{3}|\u2026|comando|command)(?:\s+(?:real|completo|concreto|here|concrete|complete))?['\"]?$",
        )
        if any(
            re.fullmatch(pattern, candidate)
            for candidate in (lowered, lowered_without_ticks)
            for pattern in patterns
        ):
            return True

        return False

    def _extract_app_server_final_message(self, turn):
        messages = []
        for item in turn.get("items", []):
            if item.get("type") == "agentMessage":
                text = item.get("text")
                if text:
                    messages.append(text)
        return "\n\n".join(messages)

    def _format_codex_app_server_error(self, message):
        lower = (message or "").lower()
        if "cancelada pelo usuario" in lower:
            return "Tarefa cancelada."
        if "sem enviar progresso" in lower:
            return message
        if self._is_codex_capacity_error(lower):
            return (
                "O Codex esta com alta demanda no momento e nao conseguiu responder agora. "
                "A IDE esta funcionando; esse erro vem do servico Codex. "
                "Tente novamente em alguns minutos."
            )
        if "not logged in" in lower or "unauthorized" in lower:
            return (
                "O Codex que a IDE chama ainda nao esta logado no Windows. "
                "Clique em `Entrar Codex`, conclua o login do Codex CLI e tente novamente."
            )
        return f"Codex app-server retornou erro:\n\n{message}"

    def _generate_codex_exec_solution(
        self,
        executable,
        prompt,
        image_path=None,
        code_context=None,
        stream_callback=None,
        reasoning_effort=None,
        workspace_path=None,
    ):
        workspace = Path(workspace_path).resolve() if workspace_path else Path.cwd()
        selected_effort = (reasoning_effort or self.codex_reasoning_effort or "xhigh").strip().lower()
        prompt_text = (
            f"{self.system_instruction}\n\n"
            "Voce esta sendo chamado diretamente pela Merotec IA IDE via Codex CLI.\n"
            f"Use raciocinio altissimo nesta tarefa: effort={selected_effort}.\n"
            "Pode editar arquivos no workspace quando a tarefa pedir implementacao.\n"
            "Nao responda com promessa. Execute a tarefa e devolva apenas resultado final ou a acao real necessaria.\n"
            "Ao terminar, responda em portugues com um resumo curto do que fez.\n\n"
            f"{self._message_payload(prompt, code_context)}"
        )

        output_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as file:
                output_path = Path(file.name)

            command = [
                executable,
                "exec",
                "-c",
                'model_provider="openai"',
                "-c",
                f'model="{self.codex_model_name or "gpt-5.5"}"',
                "-c",
                f'model_reasoning_effort="{selected_effort}"',
                "-c",
                "sandbox_workspace_write.network_access=true",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "-o",
                str(output_path),
            ]
            if self.codex_model_name:
                command.extend(["--model", self.codex_model_name])
            if image_path and os.path.exists(image_path):
                command.extend(["--image", image_path])
            command.append("-")

            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(workspace),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.active_process = process
            process.stdin.write(prompt_text)
            process.stdin.close()

            if stream_callback:
                stream_callback("Codex iniciou a tarefa...\n")

            output_chunks = []
            buffer = []
            started_at = time.time()
            while True:
                if self.cancel_requested:
                    self.cancel_generation()
                    return "Tarefa cancelada."
                chunk = process.stdout.read(1)
                if not chunk:
                    break
                output_chunks.append(chunk)
                buffer.append(chunk)
                if stream_callback and (chunk in {"\n", "\r"} or len(buffer) >= 160):
                    stream_callback("".join(buffer))
                    buffer.clear()
                if time.time() - started_at > 1800:
                    self.cancel_generation()
                    return "Codex demorou mais de 30 minutos nessa tarefa. Divida em passos menores e tente novamente."

            if stream_callback and buffer:
                stream_callback("".join(buffer))
            process.wait(timeout=5)
            output = "".join(output_chunks)
            final_message = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""

            if process.returncode != 0:
                tail = self._compact_command_error(output)
                lower_tail = tail.lower()
                if "401 unauthorized" in lower_tail or "missing bearer" in lower_tail or "not logged in" in lower_tail:
                    return (
                        "Codex esta instalado, mas a sessao nao esta autenticada para executar tarefas. "
                        "Clique em `Entrar Codex`, conclua o login e tente novamente."
                    )
                if self._is_codex_capacity_error(lower_tail):
                    return (
                        "O Codex esta com alta demanda no momento e nao conseguiu responder agora. "
                        "A IDE esta funcionando; esse erro vem do servico Codex. "
                        "Tente novamente em alguns minutos."
                    )
                return f"Codex retornou erro {process.returncode}.\n\n{tail}"

            return final_message or (output or "").strip() or "Codex terminou sem mensagem final."
        except subprocess.TimeoutExpired:
            self.cancel_generation()
            return "Codex demorou mais de 30 minutos nessa tarefa. Divida em passos menores e tente novamente."
        except Exception as exc:
            return f"Erro ao executar Codex local: {exc}"
        finally:
            if output_path:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if self.active_process is locals().get("process"):
                self.active_process = None

    def _compact_command_error(self, output):
        lines = (output or "").strip().splitlines()
        interesting = []
        for line in lines:
            clean = line.strip()
            lower = clean.lower()
            if not clean:
                continue
            if (
                "error" in lower
                or "unauthorized" in lower
                or "not logged in" in lower
                or "missing bearer" in lower
                or "usage:" in lower
                or "unexpected argument" in lower
                or clean.startswith("ERROR:")
            ):
                interesting.append(clean)

        selected = interesting[-12:] if interesting else lines[-20:]
        text = "\n".join(selected).strip()
        return text[-2400:] if len(text) > 2400 else text

    def _is_codex_capacity_error(self, text):
        capacity_markers = [
            "high demand",
            "temporar",
            "reconnecting",
            "service unavailable",
            "rate limit",
            "overloaded",
        ]
        return any(marker in text for marker in capacity_markers)

    def _generate_openai_solution(self, prompt, image_path=None, code_context=None):
        if not self.openai_api_key:
            return (
                "Para usar GPT na IDE, abra Configurar IA e cole uma OPENAI_API_KEY. "
                "Login no ChatGPT/Codex nao pode ser usado diretamente como chave de API."
            )

        try:
            payload = {
                "model": self.model_id,
                "instructions": self.system_instruction,
                "input": self._openai_input(prompt, code_context, image_path),
                "reasoning": {"effort": "high"},
            }
            request = urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            self._remember_openai_usage(data.get("usage"))
            return self._extract_openai_text(data)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return self._format_openai_http_error(exc.code, body)
        except Exception as exc:
            return f"Erro no motor OpenAI usando modelo `{self.model_id}`: {exc}"

    def _format_openai_http_error(self, status_code, body):
        try:
            payload = json.loads(body)
            message = payload.get("error", {}).get("message", body)
            code = payload.get("error", {}).get("code", "")
        except json.JSONDecodeError:
            message = body
            code = ""

        if status_code == 401 or code == "invalid_api_key":
            return (
                "A chave da OpenAI esta invalida ou expirada. Abra Configurar IA e cole uma nova "
                "OPENAI_API_KEY criada no painel da OpenAI."
            )

        if status_code == 429 and code == "insufficient_quota":
            self.latest_quota_problem = "sem cota disponivel"
            self.latest_quota_updated_at = time.time()
            return (
                "Sua chave foi aceita, mas a conta/projeto esta sem cota disponivel. "
                "Verifique Billing, Usage e Limits na plataforma da OpenAI."
            )

        if status_code in {400, 404} and "model" in message.lower():
            return (
                f"O modelo `{self.model_id}` nao foi aceito pela API da sua conta. "
                "Abra Configurar IA e teste `gpt-5.2` ou outro modelo liberado para voce."
            )

        return f"Erro no motor OpenAI usando modelo `{self.model_id}`: HTTP {status_code} - {message}"

    def _extract_openai_text(self, data):
        if data.get("output_text"):
            return data["output_text"]

        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if text:
                    chunks.append(text)
        return "\n".join(chunks)

    def _generate_google_solution(self, prompt, image_path=None, code_context=None):
        if not GoogleClient:
            return "O pacote Google GenAI nao esta instalado nesse Python. Use Codex ou OpenAI, ou instale google-genai."
        if not self.chat_session:
            return (
                "Configure GOOGLE_API_KEY ou altere AI_PROVIDER=openai com OPENAI_API_KEY. "
                "Depois reinicie a IDE."
            )

        try:
            parts = [self._message_payload(prompt, code_context)]

            if image_path and os.path.exists(image_path):
                parts.append(PIL.Image.open(image_path))

            response = self.chat_session.send_message(parts)
            return response.text or ""
        except Exception as exc:
            if "429" in str(exc):
                self.reset_session()
                return "A sessao ficou pesada ou limitada pela API. Reiniciei o chat; tente novamente."
            return f"Erro no motor GenAI: {exc}"

    def reset_session(self):
        if self.provider in {"codex", "openai"} or not self.client:
            return
        self.chat_session = self.client.chats.create(
            model=self.model_id,
            config=self.generation_config,
        )

    def cancel_generation(self):
        self.cancel_requested = True
        process = self.active_process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
