# Merotec IA IDE

Projeto Python desktop com interface em PySide6 para automacao assistida por IA,
gerenciamento de projetos, execucao de comandos, navegador interno e recursos de voz.
## Interface antiga em customtkinter

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/3d1391d1-e5d6-4e6d-af41-9fe28b041629" />

## Nova interface PySide6

<img width="1919" height="1013" alt="image" src="https://github.com/user-attachments/assets/62908d27-b26a-4af2-9783-57446523791a" />


## Modelos de IA nas configurações

Configurações da IA agora descobre modelos automaticamente para **Codex local** e
**API compatível com OpenAI**, com listas pesquisáveis/editáveis, atualização manual,
IDs exatos e níveis de raciocínio Codex. Salvar aplica o perfil às próximas solicitações.
O ID identificado fica visível no cabeçalho e na barra inferior, inclusive no modo
automático, e acompanha o modelo informado durante a execução.
Veja o [guia de descoberta e troca](docs/model-discovery.md).

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

## Vídeos e narração

O menu de anexo mantém dois fluxos independentes: **Gerar vídeo com IA...**
para renderizar um vídeo pelo provedor local configurado, e **Criar roteiro de
vídeo...** para planejar o material com o assistente. A mensagem `Gere um
vídeo: ...` também aciona a geração, sem interferir na análise de vídeos
anexados.

A geração local usa um ComfyUI em `http://127.0.0.1:8188`. Em
**Configurações da IA**, informe a URL e o caminho do workflow JSON exportado
pelo ComfyUI. O workflow deve incluir `$PROMPT`; os tokens opcionais são
`$WIDTH`, `$HEIGHT`, `$DURATION_SECONDS`, `$QUALITY` e `$REFERENCE_IMAGE`.
Os vídeos são salvos em `.merotec_system_ai/generated_videos/`, aparecem no
chat com reprodução e podem ser abertos ou salvos em outro local.

Para analisar uma gravação de tela, anexe o vídeo e peça, por exemplo,
`Analise este vídeo e crie uma narração natural`. A IDE extrai telas em ordem
cronológica, cria o roteiro com base apenas no que aparece nelas e gera o áudio
no chat.

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

## Engenharia elétrica e eletrônica

**Firmware nativo Python:** a bancada agora usa **Merotec Python (nativo)** por
padrão, com instalação gerida de pacotes AVR, build, gravação e monitor serial
sem Arduino CLI/PIO. Suporta Uno/Nano/Mega AVR e um subconjunto de `platformio.ini`.
Veja o [guia e limites do gerenciador próprio](docs/native-firmware.md).

**Código Arduino:** busque a pasta/arquivo de firmware ou use a aba **Código Arduino**
para criar sketches a partir de modelos, colar/editar código, salvar, compilar e
enviar à placa. Logs e monitor serial aparecem no painel inferior da bancada.

**Simulador Arduino:** execute o HEX compilado de Uno/Nano ATmega328P em um emulador
offline, com seleção de LEDs, relés, motor, I/O, 7 segmentos, visor serial e analisador
lógico. Há entradas digitais/ADC, UART virtual e controles de pausa/passo/reinício.
Veja o [guia e limites da simulação](docs/arduino-simulator.md); os modelos didáticos
de cargas não reproduzem toda a física do dispositivo.

**CAD nativo Python:** abra **Engenharia > Editor nativo de esquemático e PCB**
para criar componentes, redes e rotas em camada única SMD, com ERC/DRC interno,
desfazer/refazer, salvamento `.mpcb.json` e exportação SVG/BOM. Não requer KiCad.
Veja o [guia e limites do editor nativo](docs/native-pcb.md).

**Testes PCB e arquitetura:** na bancada, a aba **PCB e arquitetura** verifica BOM,
pinagem, alimentação, orçamento de corrente, interfaces e medições registradas.
A opção KiCad executa ERC/DRC quando `kicad-cli` está no PATH. Gera relatórios TXT/JSON
com resultados aprovados, falhas e pendências. Veja o [formato dos dados e exemplos](docs/pcb-diagnostics.md).

A interface PySide6 oferece **Engenharia > Bancada eletrônica** com:

- Cálculos offline de lei de Ohm, potência DC, resistor de LED e filtro RC, com unidades explícitas.
- Firmware: listar dispositivos, compilar, compilar/gravar e monitor serial com saída no painel inferior da bancada.
- Novos projetos `arduino`, `esp32`, `stm32`, `micropython` e `electronics` em Arquivo > Novo projeto.
- BOM e pinagem CSV, dossiê de engenharia e plano de ensaios para preencher em cada novo projeto.
- Especialista no chat: `/skill electrical-electronics-engineer desenvolva ...`; seleção automática para pedidos de eletrônica, firmware e automação.
- Autocompletar local Arduino em `.ino` e vocabulário C em `.c`/`.h`.

Para o backend nativo AVR, use **Preparar toolchain AVR da IDE**. Para os backends
externos opcionais, instale Arduino CLI/PlatformIO e os pacotes da placa no PATH.
A bancada mostra quais ferramentas estão presentes e não grava placas ao abrir.
Arduino usa `firmware/firmware.ino` e Uno como configuração inicial; ESP32 usa `esp32dev`, STM32 usa `nucleo_f401re`.
Adapte a configuração, pinagem e níveis elétricos à placa real. O envio Arduino compila antes de gravar.
O monitor usa o painel da bancada até ser interrompido. Salve os arquivos antes de compilar.
MicroPython oferece estrutura inicial e assistência no chat; transferência e REPL dependem da ferramenta da placa.

A IDE usa uma [camada comum de execução, memória e diagnóstico](docs/execution-diagnostics.md)
para deploy, Git, compilação, scripts, testes, ferramentas da bancada e ações de código.
O terminal persistente registra o código de saída real e preserva ambiente e diretório.
Tentativas automáticas repetidas exigem mudança de contexto ou diagnóstico novo;
tentativas manuais continuam disponíveis. Apenas os testes Python simples usam
AppData e temporários isolados: Git/deploy preservam autenticação e perfis.

Os cálculos são ideais e os projetos são pontos de partida: não incluem circuito físico validado
ou certificação industrial. O CAD nativo cobre esquemáticos genéricos e PCB de camada única SMD;
KiCad/SPICE/HDL continuam opcionais para fluxos além desse escopo. Registre hipóteses, datasheets e medições no dossiê.
