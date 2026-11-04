# Merotec Firmware — gerenciador próprio em Python

Na bancada, **Firmware e serial** usa **Merotec Python (nativo)** por padrão.
O gerenciador é código da IDE; não chama Arduino CLI ou PlatformIO para instalar
pacotes, preparar sketches, compilar, descobrir bibliotecas, gravar ou monitorar.
Os dois backends anteriores continuam disponíveis como alternativas opcionais.

## Preparar e usar

### Criar, procurar e editar código Arduino

Em **Firmware e serial**, **Buscar pasta…** seleciona a pasta do firmware. Se houver
um sketch `.ino` na pasta (ou na subpasta `firmware/`), ele abre na aba **Código
Arduino**. **Abrir código…** permite buscar um arquivo individual, inclusive fora
do workspace ativo, sem trocar o workspace da IDE.

Na aba **Código Arduino**:

- **Novo código** abre um modelo editável em memória: vazio, LED sem bloqueio,
  entrada analógica, botão com pull-up ou eco serial.
- **Criar sketch…** cria uma pasta nova e um `.ino` com o mesmo nome, sem sobrescrever
  sketches existentes. Esse formato também atende o layout convencional Arduino.
- **Colar** ou Ctrl+V permite usar seu próprio código; há realce C/C++, desfazer e refazer.
- **Salvar**, Ctrl+S e **Salvar como…** gravam o arquivo. O editor avisa sobre alterações
  não salvas e mudanças detectadas no disco antes de substituir o código.
- **Salvar e compilar** salva o conteúdo atual e executa o backend selecionado.
- **Salvar e enviar à placa** salva, valida placa/porta e pede confirmação da gravação.
  O backend nativo compila antes de gravar e para se a compilação falhar.
- **Configurar placa/porta** retorna à configuração; **Monitor serial** usa a mesma
  porta e baud selecionados. As saídas continuam no painel inferior.

O botão **Novo código** não escreve no disco até salvar. Escolha **Criar sketch…**
quando quiser preparar uma pasta Arduino completa. Ao usar o backend externo
Arduino CLI, mantenha o nome do `.ino` principal igual ao da pasta.

O editor trabalha com um arquivo por vez; abra outros `.ino`, `.cpp`, `.c`, `.h` ou
`.hpp` pelo seletor para editar os demais fontes. Ele não infere pinagens nem faz
simulação física. Confira os pinos e requisitos elétricos dos exemplos antes de usar.

### Compilar e comunicar com a placa

1. Selecione a pasta de fontes. Nos novos projetos Arduino, ela é `firmware/`.
2. Clique em **Preparar toolchain AVR da IDE** na primeira utilização. A IDE baixa
   core Arduino AVR 1.8.6, AVR GCC e avrdude, selecionando os arquivos da plataforma
   a partir do [índice oficial](https://downloads.arduino.cc/packages/package_index.json).
   Cada arquivo é conferido contra seu SHA-256 antes de extrair. Os pacotes e suas
   licenças ficam em `.merotec_system_ai/firmware_tools/` na pasta da IDE, com
   recibo `receipt.json`. A instalação não altera PATH nem exige CLI/PIO.
3. Informe a placa como `uno`, `nano`, `nano-old` ou `mega2560`; os FQBN AVR
   correspondentes também são aceitos. Manifestos do projeto têm prioridade.
4. Salve seus arquivos e clique em **Compilar firmware**. Os logs aparecem no
   painel inferior **Saída da bancada**, na própria janela. Ao concluir, o gerenciador informa uso de Flash e RAM estática.
5. Para gravar, informe explicitamente a porta e use **Compilar e gravar**.
   A interface pede confirmação e uma falha de compilação impede chamar o gravador.
6. O campo **Porta** lista os dispositivos detectados ao abrir a bancada. Use
   **Atualizar portas** após conectar ou remover dispositivos; a seleção atual
   é preservada, inclusive se a porta deixar de ser detectada. O campo **Baud**
   oferece taxas predefinidas, inicialmente 115200. Ambos aceitam digitação manual.
   Use **Listar dispositivos** para listar portas também no painel de saída. O gerenciador não
   deduz o modelo da placa a partir da porta. **Monitor serial** usa as APIs do
   Windows/POSIX diretamente, sem pyserial ou CLIs. Ele é de leitura, em UTF-8 com
   substituição de bytes inválidos, 8N1 e sem controle de fluxo.

O gerenciador e a integração serial são Python. O compilador C/C++ e o gravador
continuam sendo os binários especializados AVR GCC e avrdude, geridos pela IDE;
não são reimplementações Python desses projetos. Drivers USB e uma placa com
bootloader compatível continuam necessários para gravação. No Windows, abrir o
monitor habilita DTR/RTS e pode reiniciar a placa. Use **Interromper** no painel inferior
antes de iniciar outro monitor ou uma gravação.

A janela permanece aberta durante as operações. O painel inferior pode ser
redimensionado pelo divisor, registra stdout/stderr, mostra o estado do processo e
oferece **Limpar saída**. Uma operação roda por vez na bancada, independentemente
do Terminal Local da IDE. Ao fechar a bancada, seu processo é interrompido.

## Manifesto nativo

`merotec-firmware.json` na pasta selecionada:

```json
{
  "version": 1,
  "board": "uno",
  "source_dir": ".",
  "include_dir": "include"
}
```

Os caminhos devem permanecer dentro do projeto. Com `source_dir: "."`, o gerenciador
compila fontes C/C++/Assembly da raiz e de `src/`, além das abas `.ino` da raiz.
A aba com o mesmo nome da pasta vem primeiro, seguida pelas outras em ordem
alfabética. O gerenciador inclui `Arduino.h` e preserva localizações via `#line`.
Ele **não gera protótipos automaticamente**: declare funções antes do uso ou
adicione protótipos, como em C++ convencional.

Bibliotecas são descobertas pelos includes em `libraries/`, `lib/` e nas bibliotecas
do core instalado. Cada biblioteca usa layout `src/` ou layout legado na raiz;
includes transitivos também são examinados. Bibliotecas com metaprogramação de
includes ou layouts incomuns podem exigir adaptação. Não há catálogo/download
automático de bibliotecas de terceiros nem resolução de versões de `lib_deps`.

O build recompila todos os fontes usados. Arquivos ELF, HEX e o resumo `build.json`
ficam em `.merotec_system_ai/firmware_build/<placa>/` no projeto. Limites de Flash
e RAM estática são verificados antes de gravar; a RAM dinâmica/pilha não é prevista.

## Configuração estilo PlatformIO sem executar PIO

Na ausência de manifesto nativo, este subconjunto de `platformio.ini` é interpretado
diretamente pelo gerenciador Python:

```ini
[platformio]
default_envs = uno
src_dir = src
include_dir = include

[env:uno]
platform = atmelavr
board = uno
framework = arduino
monitor_speed = 115200
```

Campos `[env]` são herdados pelos ambientes específicos. Se houver vários ambientes,
informe um nome no campo **Ambiente** ou um único `default_envs`.
Placas aceitas: `uno`, `nanoatmega328`, `nanoatmega328new`, `megaatmega2560`.
`monitor_speed` e `upload_port` são aceitos para leitura do projeto; os campos
Porta e Baud da bancada comandam as ações seriais e não são preenchidos por eles.
Outras opções, como `lib_deps`, `build_flags`, `extra_scripts` e `extra_configs`, são
recusadas com mensagem explícita. Esse suporte não implementa toda a plataforma PIO.

ESP32, STM32, Uno R4 e outros MCUs ainda não têm backend nativo neste gerenciador.
Os templates anteriores permanecem utilizáveis com o backend externo apropriado.
Perfis AVR assumem clock de 16 MHz e bootloader padrão da placa selecionada.

## Linha de comando

```powershell
venv\Scripts\python.exe -m modules.native_firmware install
venv\Scripts\python.exe -m modules.native_firmware status
venv\Scripts\python.exe -m modules.native_firmware boards
venv\Scripts\python.exe -m modules.native_firmware build --project "C:\projeto\firmware" --board uno
venv\Scripts\python.exe -m modules.native_firmware upload --project "C:\projeto\firmware" --board uno --port COM3
venv\Scripts\python.exe -m modules.native_firmware monitor --port COM3 --baud 115200
```

Os comandos de compilação e gravação usam listas de argumentos sem shell. O modo
de gravação por linha de comando exige pedido explícito, mas não apresenta janela
de confirmação. A instalação requer internet; após instalar, builds AVR podem
funcionar offline. Não há alteração de fuses nem gravação de bootloader.

As escolhas de flags e perfis seguem o [core AVR oficial](https://github.com/arduino/ArduinoCore-avr/tree/1.8.6)
e a [especificação de plataforma Arduino](https://docs.arduino.cc/arduino-cli/platform-specification/).
