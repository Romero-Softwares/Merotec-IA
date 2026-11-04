# Simulador Arduino integrado

Na bancada, abra **Simulador Arduino**, ou use **Simular** na aba **Código Arduino**.
O fluxo salva o arquivo, compila pelo gerenciador nativo e carrega o HEX resultante
no emulador offline. Ele não grava nem abre portas físicas. As saídas UART e erros
aparecem no painel inferior da bancada.

O simulador executa instruções AVR do firmware compilado, em vez de procurar padrões
no texto do sketch ou transformar C++ em Python. Funções, loops, variáveis e decisões
são os do binário carregado. O relógio e os timers simulados fazem `millis()`,
`delay()`, GPIO, PWM, ADC, interrupções externas e UART avançarem em tempo virtual.
Bibliotecas funcionam apenas quando seus periféricos e conexões têm modelos
compatíveis; não se deve presumir compatibilidade universal.

## Fluxo de aprendizado

1. Em **Código Arduino**, escolha um exemplo e clique em **Novo código**. Há exemplos
   para **5 LEDs / 3 efeitos**, motor PWM com botão, relés sequenciais e 7 segmentos.
2. Em **Firmware e serial**, selecione perfil `uno`, `nano` ou `nano-old`, ou o FQBN
   equivalente, e prepare os pacotes AVR caso ainda não estejam instalados.
3. Volte ao código e clique em **Simular**. Escolha onde salvar caso ainda seja um
   buffer novo. Uma compilação com erro não inicia o firmware anterior.
4. Selecione o painel e os pinos associados. O exemplo de cinco LEDs usa D2–D6.
   Números P0–P13 são D0–D13; P14–P19 são A0–A5.
5. Use **Pausar**, **Passo**, **Reiniciar** e **Encerrar**. A pausa é confirmada depois
   de concluir o lote atual de instruções. O passo avança 10 ms virtuais (160.000
   ciclos), não uma única linha C++ ou instrução.
6. Abra as entradas digitais/analógicas para mudar níveis ou ADC. O botão momentâneo
   força o pino selecionado para GND enquanto pressionado e o libera ao soltar.
7. O campo de envio UART injeta texto com LF na serial simulada; não usa COM/USB.

Também é possível abrir um HEX já compilado com **Abrir HEX…**. Ele deve ser para
ATmega328P a 16 MHz; o formato HEX não permite inferir com segurança a placa de origem.
O carregador verifica checksums, limites de Flash e registro de fim, mas isso não
prova compatibilidade de arquitetura. O binário tem de corresponder ao alvo.

## Painéis

| Painel | Representação |
| --- | --- |
| LEDs (5) | Estado/brilho a partir do duty médio observado nas saídas selecionadas |
| Relés (4) | Bobina lógica ON/OFF e contatos ideais COM–NO/NC; opção ativo LOW |
| Motor DC + ponte H | PWM/enable, IN1, IN2, direção e animação ilustrativa |
| Entradas / saídas | D0–D13 e A0–A5, nível, direção e conflitos de acionamento |
| Display 7 segmentos | Sete saídas para a–g, ativo HIGH ou LOW |
| Display serial 16×2 | Últimas linhas UART em um visor; não é um LCD HD44780/I²C |
| Analisador lógico | Histórico limitado de amostras do duty por pino, com tempo virtual |

Entradas **Flutuante** seguem o pull-up quando `INPUT_PULLUP` está ativo. Sem pull-up,
o modelo resolve a entrada flutuante como LOW; não reproduz ruído ou aleatoriedade
de entradas físicas desconectadas. ADC usa referência ideal de 5 V e controles
0–1023. Conflitos entre uma fonte digital externa e uma saída oposta são indicados,
sem calcular corrente de curto-circuito.

## Alcance e limites

- Alvo inicial: Uno/Nano com ATmega328P, Flash de 32 KB, RAM de 2 KB e clock de 16 MHz.
  Mega, ESP32, STM32 e Uno R4 não são alvos deste simulador.
- Motor de emulação: [AVR8js 0.21.1](https://github.com/wokwi/avr8js), executado no
  V8 incorporado ao Python por [mini-racer](https://pypi.org/project/mini-racer/).
  A interface, compilação, controles e integração são Python/PySide6.
  Não requer Node, navegador, API ou conexão online depois de instalar a toolchain.
- O código de terceiros fica em `modules/vendor/avr8js.js`, com licença MIT e recibo
  da versão e SHA-512 ao lado. Não é uma implementação própria completa do núcleo AVR.
- A simulação roda em thread separada, com lotes limitados de ciclos e agendamento
  baseado no relógio monotônico do computador. A velocidade padrão é **1× — tempo
  real**. Também há 0,25×, 0,5×, 2× e Máximo. Os timers da MCU continuam a 16 MHz:
  o agendamento controla a execução das instruções, sem falsificar `millis()`.
  Pausas não contam como tempo executado e retomar não salta o relógio virtual.
- O status compara segundos simulados/reais, velocidade média efetiva, alvo e
  motor em uso. Se o computador não acompanhar o alvo, indica velocidade abaixo
  da solicitada; não pula instruções para esconder atraso. Os tempos reais exibidos
  excluem pausas. Alterações de velocidade reancoram o agendamento sem saltos.
- Sem mini-racer, o QJSEngine do Qt permanece como modo de compatibilidade, com
  aviso no status, e pode ser mais lento. Execute a instalação das dependências
  da IDE para disponibilizar o runtime rápido. Não é necessário Node ou navegador.
- O painel sempre representa o HEX carregado. Após editar o código, compile e simule
  novamente. Reiniciar recarrega o mesmo HEX, não recompila o texto do editor.
- Mapeamento dos painéis é manual; não é extraído da PCB nem da BOM. As redes e cargas
  analógicas do circuito não são resolvidas por SPICE.
- Não há modelo de torque, inércia, aquecimento, tensão de bobina, atrasos de relé,
  carga mecânica, ruído, falhas de alimentação ou garantia de funcionamento físico.
  A animação do rotor não representa RPM real. Correntes de motor/relé exigem drivers
  e alimentação dimensionados quando o circuito for levado a hardware.
- Não há modelos de sensores I²C/SPI, LCD HD44780, barramentos industriais ou qualquer
  periférico arbitrário conectado. O visor serial é uma ferramenta didática separada.
- O analisador amostra estados/duty e não registra todas as bordas de alta frequência.
  As filas seriais são limitadas para evitar crescimento sem controle de memória.

Os testes incluem um firmware AVR real compilado para verificar pull-up, botão/LED,
timer `millis()`, PWM, ADC, interrupção CHANGE e eco UART, além dos controles e do
encerramento da thread. Esses testes são de emulação; não substituem medições em placa.
