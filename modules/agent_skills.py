"""Especialistas determinísticos para o agente interno da Merotec IA IDE.

Os perfis de IA escolhem *provedores* (Codex, OpenAI, modelo local etc.). Este
módulo resolve uma camada diferente: qual especialidade de engenharia ajuda a
missão atual. O roteamento é local e previsível; ele não consome uma chamada de
modelo nem concede ferramentas ou permissões adicionais.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


SUPPORTED_AGENT_ACTIONS = frozenset(
    {
        "PATCH",
        "READ",
        "SEARCH_TEXT",
        "WRITE_PART",
        "WRITE",
        "REPLACE",
        "INSERT_BEFORE",
        "INSERT_AFTER",
        "VALIDATE",
        "EXECUTE",
        "HUMAN_TEST",
        "OPEN_URL",
        "BROWSER_INSPECT",
        "BROWSER_CLICK",
        "BROWSER_TYPE",
        "BROWSER_SCROLL",
    }
)

READ_ONLY_AGENT_ACTIONS = frozenset({"READ", "SEARCH_TEXT"})


@dataclass(frozen=True)
class AgentSkill:
    """Uma especialidade que pode orientar uma missão sem ampliar poderes."""

    id: str
    title: str
    description: str
    instruction: str
    triggers: tuple[str, ...] = ()
    project_kinds: tuple[str, ...] = ()
    allowed_actions: frozenset[str] = SUPPORTED_AGENT_ACTIONS
    priority: int = 0


@dataclass(frozen=True)
class SkillResolution:
    """Resultado imutável para manter a mesma especialidade durante a missão."""

    skill: AgentSkill
    source: str
    clean_prompt: str


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def _skill(
    identifier: str,
    title: str,
    description: str,
    instruction: str,
    *,
    triggers: tuple[str, ...] = (),
    project_kinds: tuple[str, ...] = (),
    allowed_actions: frozenset[str] = SUPPORTED_AGENT_ACTIONS,
    priority: int = 0,
) -> AgentSkill:
    return AgentSkill(
        id=identifier,
        title=title,
        description=description,
        instruction=instruction.strip(),
        triggers=triggers,
        project_kinds=project_kinds,
        allowed_actions=frozenset(allowed_actions) & SUPPORTED_AGENT_ACTIONS,
        priority=priority,
    )


BUILTIN_AGENT_SKILLS = (
    _skill(
        "general-engineer",
        "Engenharia geral",
        "Implementa e valida mudanças de software com escopo controlado.",
        "Entenda o objetivo, leia somente o contexto necessário e escolha a menor mudança verificável. "
        "Preserve o trabalho existente e termine com evidência real de validação quando a tarefa pedir alteração.",
    ),
    _skill(
        "merotec-ide-maintainer",
        "Manutenção da Merotec IDE",
        "Especialista em Python, PySide6, Codex app-server e ciclo interno de agentes.",
        "Trate a IDE como um sistema de execução assíncrona: preserve a separação entre thread de IA e thread Qt, "
        "mantenha o protocolo anunciado compatível com o executor real e cubra regressões com testes focados. "
        "Não enfraqueça validações, fronteiras de workspace ou aprovações para fazer um caso de teste passar.",
        triggers=("merotec", "pyside", "pyside6", "qt", "codex", "app-server", "agente interno", "ide"),
        project_kinds=("python",),
        priority=7,
    ),
    _skill(
        "electrical-electronics-engineer",
        "Engenharia elétrica, eletrônica e sistemas embarcados",
        "Desenvolvimento pessoal e industrial: circuitos, instrumentação, PCB, firmware e automação.",
        "Conduza requisitos, diagrama de blocos, seleção de componentes, cálculos com unidades e tolerâncias, "
        "BOM, pinagem, firmware e plano de ensaios. Trabalhe com Arduino/C/C++, ESP32/ESP-IDF, STM32, "
        "MicroPython, Raspberry Pi, RTOS, HDL, SPICE e KiCad conforme a ferramenta real do projeto. "
        "Para automação considere sensores, 4–20 mA, 0–10 V, RS-485/Modbus, CAN, CLP e IEC 61131-3. "
        "Confirme datasheets e revisões; não invente pinagens, limites absolutos ou conformidade normativa. "
        "Declare hipóteses e diferencie simulação, compilação e medição física. Não afirme testar hardware ausente. "
        "Use estados seguros, watchdog, temporização não bloqueante, proteção e isolamento adequados. "
        "Não grave placas nem acione atuadores sem pedido explícito; preserve os guardas do executor. "
        "Em rede elétrica e funções críticas, indique revisão por profissional habilitado e ensaios antes da operação.",
        triggers=("arduino", "esp32", "stm32", "micropython", "eletronica", "eletrico", "eletrica", "circuito", "firmware", "kicad", "pcb", "modbus", "clp", "embedded", "fpga", "verilog", "vhdl", "platformio", "esp-idf", "sensor", "rs-485"),
        project_kinds=("arduino", "platformio", "esp32", "stm32", "micropython", "electronics"),
        priority=7,
    ),
    _skill(
        "python-pyside-maintainer",
        "Python e PySide6",
        "Manutenção de aplicações Python desktop e fluxos Qt.",
        "Isole a causa em vez de reescrever módulos grandes. Em alterações Qt, mantenha widgets na thread da interface "
        "e devolva resultados assíncronos ao event loop. Valide sintaxe e execute o menor teste que cobre o fluxo afetado.",
        triggers=("python", "pyqt", "pyside", "pyside6", "qwidget", "qprocess", "qt"),
        project_kinds=("python",),
        priority=5,
    ),
    _skill(
        "web-ui-visual",
        "Interface web e validação visual",
        "Especialista em HTML, CSS, JavaScript e comportamento visual no navegador.",
        "Priorize o fluxo visível e os estados de interação. Faça mudanças locais em markup, estilos ou comportamento, "
        "depois valide o arquivo e use o navegador ou teste visual quando a aparência, a responsividade ou o fluxo for relevante.",
        triggers=("html", "css", "javascript", "typescript", "frontend", "front-end", "layout", "ui", "ux", "responsivo", "visual"),
        project_kinds=("web", "javascript"),
        priority=5,
    ),
    _skill(
        "debug-test-repair",
        "Depuração e testes",
        "Especialista em reproduzir falhas, corrigir a causa e prevenir regressão.",
        "Comece pela evidência: saída de teste, mensagem de erro ou caminho de reprodução. Localize a causa provável, "
        "faça a correção mínima e execute uma validação que falharia antes da mudança. Não declare sucesso sem resultado observável.",
        triggers=("bug", "erro", "falha", "quebrado", "nao funciona", "não funciona", "teste", "testes", "regressao", "regressão", "traceback", "exception"),
        priority=6,
    ),
    _skill(
        "architecture-review-readonly",
        "Revisão de arquitetura",
        "Analisa arquitetura, dependências e riscos sem modificar o workspace.",
        "Faça uma análise baseada em evidências do projeto: pontos de entrada, limites entre módulos, fluxos de dados, "
        "riscos e melhorias priorizadas. Esta especialidade é somente leitura; não altere arquivos nem execute comandos mutantes.",
        triggers=("arquitetura", "architecture", "avaliar projeto", "analisar projeto", "analise do projeto", "análise do projeto", "code review"),
        allowed_actions=READ_ONLY_AGENT_ACTIONS,
        priority=8,
    ),
    _skill(
        "security-review-readonly",
        "Revisão de segurança",
        "Audita exposição de segredos, permissões e superfícies de ataque sem modificar o projeto.",
        "Examine somente o que é necessário para apontar riscos com evidência. Não revele segredos encontrados, não execute "
        "ações externas e não altere arquivos neste modo; explique impacto, prioridade e uma correção segura recomendada.",
        triggers=("seguranca", "segurança", "vulnerabilidade", "security", "secret", "segredo", "credencial", "token", "api key", "chave de api"),
        allowed_actions=READ_ONLY_AGENT_ACTIONS,
        priority=9,
    ),
)


class AgentSkillRegistry:
    """Resolve um único especialista por tarefa e expõe um contrato compacto."""

    def __init__(self, skills=BUILTIN_AGENT_SKILLS):
        self._skills = tuple(skills)
        self._by_id = {skill.id: skill for skill in self._skills}
        if "general-engineer" not in self._by_id:
            raise ValueError("A skill general-engineer e obrigatória.")

    def list(self) -> tuple[AgentSkill, ...]:
        return self._skills

    def get(self, skill_id: object) -> AgentSkill | None:
        key = _normalized(skill_id).replace(" ", "-")
        return self._by_id.get(key)

    def consume_explicit_selection(self, prompt: object) -> tuple[str | None, str]:
        """Consome ``/skill identificador`` somente no início da mensagem."""
        text = str(prompt or "").strip()
        match = re.match(r"^/skill\s+([a-z0-9][a-z0-9-]*)\b\s*", text, re.I)
        if not match:
            return None, text
        return match.group(1).casefold(), text[match.end() :].lstrip()

    def resolve(
        self,
        prompt: object,
        project_kind: object = "",
        preferred_id: object = "auto",
    ) -> SkillResolution:
        explicit_id, clean_prompt = self.consume_explicit_selection(prompt)
        requested = explicit_id or _normalized(preferred_id).replace(" ", "-")
        if requested and requested != "auto":
            skill = self.get(requested)
            if skill is not None:
                return SkillResolution(skill=skill, source="manual", clean_prompt=clean_prompt)

        normalized_prompt = _normalized(clean_prompt)
        normalized_kind = _normalized(project_kind)
        candidates = []
        for skill in self._skills:
            if skill.id == "general-engineer":
                continue
            trigger_hits = sum(1 for trigger in skill.triggers if _normalized(trigger) in normalized_prompt)
            kind_hit = bool(normalized_kind and normalized_kind in {_normalized(item) for item in skill.project_kinds})
            if not trigger_hits and not kind_hit:
                continue
            # Termos explícitos do pedido são mais específicos que o tipo amplo
            # do projeto. Assim, "corrija este bug" não é desviado para o
            # mantenedor genérico de Python apenas por estar numa IDE Python.
            score = (10_000 if trigger_hits else 0) + skill.priority * 100 + trigger_hits * 10 + (1 if kind_hit else 0)
            candidates.append((score, skill.id, skill))
        if candidates:
            _score, _identifier, selected = max(candidates)
            return SkillResolution(skill=selected, source="auto", clean_prompt=clean_prompt)
        return SkillResolution(skill=self._by_id["general-engineer"], source="fallback", clean_prompt=clean_prompt)

    @staticmethod
    def render_context(resolution: SkillResolution) -> str:
        skill = resolution.skill
        actions = ", ".join(sorted(skill.allowed_actions)) or "nenhuma"
        source = {"manual": "selecionado pelo usuário", "auto": "selecionado automaticamente", "fallback": "padrão"}.get(
            resolution.source,
            resolution.source,
        )
        return (
            "PERFIL ESPECIALIZADO ATIVO DA IDE\n"
            f"Nome: {skill.title} ({source})\n"
            f"Objetivo: {skill.description}\n"
            f"Instruções: {skill.instruction}\n"
            "CONTRATO DA INTERFACE ATIVA: use somente as ações abaixo quando precisar agir. "
            "Não invente tags fora desta lista; elas não são executadas por esta interface.\n"
            f"Ações disponíveis: {actions}."
        )


AGENT_SKILL_CHOICES = tuple((skill.id, skill.title) for skill in BUILTIN_AGENT_SKILLS)
