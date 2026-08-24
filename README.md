# Merotec IA IDE

Projeto Python desktop com interface em PySide6 para automacao assistida por IA,
gerenciamento de projetos, execucao de comandos, navegador interno e recursos de voz.
## Interface antiga em customtkinter

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/3d1391d1-e5d6-4e6d-af41-9fe28b041629" />

## Nova interface PySide6

<img width="1919" height="1013" alt="image" src="https://github.com/user-attachments/assets/62908d27-b26a-4af2-9783-57446523791a" />


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

## Geração de imagens

No chat, abra o menu de anexo e escolha **Gerar imagem com IA...**, ou envie uma
mensagem como `Gere uma imagem: uma cidade futurista ao entardecer`. A imagem é
gerada pelo provedor de IA selecionado e aparece no chat com prévia, além dos
botões **Abrir** e **Salvar como...**.

- Com o provedor **Codex** conectado, a IDE usa a sessão autenticada atual, sem
  exigir uma chave de API adicional.
- Com um provedor compatível com a API OpenAI, informe uma `OPENAI_API_KEY` nas
  **Configurações da IA**. A IDE envia a solicitação para o endpoint
  `/images/generations` configurado no perfil.

As imagens são copiadas para `.merotec_system_ai/generated_images/` dentro do
workspace ativo. Essa pasta é local e fica ignorada pelo Git.

## Geração local de vídeo

A conversa pode gerar vídeo pelo menu de anexo, em **Gerar vídeo com IA...**,
ou pela mensagem `Gere um vídeo: ...`. A primeira versão usa um ComfyUI local:

1. Inicie o ComfyUI com a API em `http://127.0.0.1:8188`.
2. Em **Configurações da IA**, informe a URL e o caminho do workflow JSON de vídeo exportado pelo ComfyUI.
3. Inclua `$PROMPT` no campo de texto do workflow. Os tokens opcionais são `$WIDTH`, `$HEIGHT`, `$DURATION_SECONDS`, `$QUALITY` e `$REFERENCE_IMAGE`.

O resultado é salvo em `.merotec_system_ai/generated_videos/`, aparece no chat
com controles de reprodução e pode ser aberto ou salvo em outro local. Anexe uma
imagem antes de gerar para usá-la como referência; nesse caso o workflow precisa
conter `$REFERENCE_IMAGE`.

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
- `pyside_app.py`: interface PySide6 principal. Para abrir a interface legada temporariamente, use `python main.py --legacy-tk`.
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
