# Merotec IA IDE

Projeto Python desktop com interface em CustomTkinter para automacao assistida por IA,
gerenciamento de projetos, execucao de comandos, navegador interno e recursos de voz.

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/3d1391d1-e5d6-4e6d-af41-9fe28b041629" />

## Fluxo de projetos

- `Arquivo > Novo projeto`: cria projetos vazios, Python, Flet, Web, Dart ou Flutter sem sobrescrever pastas existentes.
- `Arquivo > Abrir projeto/pasta`: troca o workspace ativo.
- `Arquivo > Abrir arquivo externo`: edita arquivos avulsos sem trocar o projeto ativo.
- Ao iniciar, a IDE restaura o ultimo projeto editado quando ele ainda existe.
- `IA > Enviar missao ao ChatGPT Web`: prepara uma missao com o mapa do workspace.
- `IA > Importar resposta do ChatGPT`: traz a resposta para a IDE e, com confirmacao, executa as acoes de codigo no projeto.

## Recursos de editor

- `Ctrl+Espaco`: autocompletar local por contexto, identificadores do arquivo e vocabulario da linguagem.
- `Ctrl+Shift+O`: navegar por classes, metodos, funcoes, headings, seletores CSS e IDs HTML.
- `Ctrl+/`: comentar ou descomentar a selecao.
- Indentacao inteligente, pares automaticos, busca, zoom, numeros de linha e marcadores de alteracao.

## Requisitos

- Python 3.11 ou superior
- Windows recomendado para a interface desktop, navegador interno e recursos de voz
- Dependencias listadas em `requirements.txt`

Dependencias pesadas ou opcionais:

- `llama-cpp-python`: necessario apenas para modelo GGUF local.
- `pywebview`: necessario para o navegador interno no Windows.
- `edge-tts`, `pyttsx3`, `sounddevice` e `SpeechRecognition`: usados por voz e TTS.
- `pywin32` e `comtypes`: integracoes especificas do Windows.

## Instalacao

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Execucao

```bash
python main.py
```

No Windows, `init_System.cmd` localiza a propria pasta e cria o ambiente virtual na
primeira execucao. O projeto pode ser movido ou clonado sem editar caminhos absolutos.

## Validacao

```bash
venv\Scripts\python.exe -m unittest discover -s tests -v
venv\Scripts\python.exe -m compileall -q main.py modules tests
venv\Scripts\python.exe -m pip check
```

O workflow `.github/workflows/ci.yml` executa essa mesma validacao no GitHub Actions.

## Sequencia de acesso

1. Abra o projeto pelo arquivo `init_System.cmd` ou execute `python main.py` no terminal.
2. Aguarde a interface carregar e confirme se o motor principal esta como `codex`.
3. Se aparecer aviso de Codex sem login, clique em `Entrar Codex`.
4. Conclua o login na janela do terminal aberta automaticamente.
5. Volte para a Merotec IA IDE e aguarde o status `Codex pronto`.
6. Abra ou selecione o workspace desejado antes de pedir alteracoes, testes ou deploy.

## Estrutura

- `main.py`: entrada principal da IDE, composicao da UI e integracao dos mixins.
- `modules/`: modulos de configuracao, motor, acoes de agente, memoria, executor, projetos, plugins e voz.
- `tests/`: suite de regressao e qualidade do repositorio.
- `docs/architecture.md`: visao da arquitetura, fluxo de IA, validacao, plugins, navegador e seguranca.
- `tcl_runtime/`: runtime Tcl/Tk local usado para estabilizar a execucao no Windows.

## Seguranca

Arquivos locais, historicos da IDE, backups, ambientes virtuais, caches, anexos, memoria
local e codigos de recuperacao PyPI estao ignorados no `.gitignore` para evitar envio de
dados temporarios ou sensiveis.

Antes de publicar, rode a validacao e confira `git status --short`.

## Publicacao no GitHub

Fluxo recomendado depois de criar o repositorio vazio no GitHub:

```bash
git remote add origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git add .
git commit -m "Preparar projeto para GitHub"
git push -u origin main
```

Se o remoto `origin` ja existir, use:

```bash
git remote set-url origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git push -u origin main
```
