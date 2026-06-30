# Arquitetura da Merotec IA IDE

## Visao geral

A Merotec IA IDE e uma aplicacao desktop Python com CustomTkinter. O arquivo `main.py`
continua sendo a entrada da interface, mas o comportamento principal esta distribuido
em mixins e modulos especializados em `modules/`.

Os pontos centrais sao:

- `main.py`: cria a janela, menus, abas, editor, terminal, navegador interno e integra os mixins.
- `modules/engine.py`: conversa com provedores de IA, Codex, modelos locais, OpenAI, Google e fallback externo.
- `modules/agent_actions.py`: interpreta acoes retornadas pela IA, aplica edicoes, executa comandos e valida entregas.
- `modules/workspace_intelligence.py`: cria briefing do workspace, resumo local, descoberta de stack e contexto de memoria.
- `modules/ai_config.py`: controla perfis de IA, status, login do Codex e configuracoes visuais.
- `modules/app_state.py`: persiste preferencias, historico de mudancas e restauracao de workspace.
- `modules/plugin_manager.py`: carrega plugins instalados por entry point e publica capacidades para a aplicacao.

## Fluxo de IA

O usuario envia uma missao pela UI. A aplicacao monta contexto com workspace atual,
historico recente, briefing inteligente, arquivos candidatos e regras operacionais.
O `UniversalEngine` gera a resposta e o `AgentActionsMixin` procura tags de acao como
`[READ]`, `[REPLACE]`, `[WRITE]`, `[EXECUTE]`, navegador e teste visual.

Cada acao real chama `mark_ai_active_action`, que registra metrica da tarefa e atualiza
a fase visivel da IA, por exemplo leitura de contexto, alteracao de arquivos, validacao
ou teste no navegador.

## Execucao e validacao

Comandos sao executados pelo executor controlado da IDE. Alteracoes de codigo passam por
validacao transacional antes de serem aceitas. Para Python, os comandos padrao evitam
varrer `.git`, `venv`, caches e artefatos locais; quando o projeto tem `main.py`,
`modules` ou `tests`, a validacao automatica usa esses alvos diretamente.

A validacao continua esta em `.github/workflows/ci.yml`:

- `python -m unittest discover -s tests -v`
- `python -m compileall -q main.py modules tests`
- `python -m pip check`

## Navegador interno

O navegador interno e conectado ao fluxo de Chat Web e tambem pode ser usado para abrir
URLs locais, inspecionar elementos e apoiar testes visuais. O runtime fica em
`modules/browser_runtime.py`, enquanto a ponte com a UI fica em
`modules/ui_web_chat_bridge.py` e `modules/web_chat_bridge.py`.

## Plugins

Plugins sao descobertos por `modules/plugin_manager.py`. A aplicacao fornece servicos
basicos ao plugin, incluindo `app`, `settings`, `workspace`, `engine`, `voice`,
`project_manager` e `executor`. Falhas de carregamento viram status reportavel no chat,
sem impedir a inicializacao da IDE.

## Memoria local e RAG

A pasta `.merotec_system_ai` guarda artefatos locais da sub-rede de memoria/RAG. Ela
serve como contexto offline e fallback extrativo quando um motor externo nao responde,
mas nao substitui sozinha um LLM generativo. O modulo `modules/workspace_intelligence.py`
resume essa memoria e injeta os trechos mais relevantes na missao.

## Dependencias opcionais

Alguns recursos dependem de bibliotecas ou runtimes pesados:

- `llama-cpp-python`: necessario apenas para GGUF local.
- `pywebview` e runtime WebView2: usados pelo navegador interno no Windows.
- `edge-tts`, `pyttsx3`, `sounddevice` e `SpeechRecognition`: usados por voz e TTS.
- `pywin32` e `comtypes`: suporte Windows para voz, janelas e automacao.

Essas dependencias ficam pinadas ou condicionadas em `requirements.txt` para reduzir
surpresas entre ambientes.

## Dados locais e seguranca

Arquivos de estado como `history.json`, `change_history.json`, `ide_settings.json`,
backups, anexos, caches, ambientes virtuais e codigos de recuperacao PyPI ficam no
`.gitignore`. Eles podem existir no computador do usuario, mas nao devem entrar no
repositorio nem no pacote de release.

Antes de publicar, rode a suite e revise o `git status` para confirmar que nenhum
arquivo local sensivel entrou no stage.
