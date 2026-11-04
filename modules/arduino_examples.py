"""Editable learning examples shared by the sketch editor and simulator guide."""

FIVE_LEDS = '''const byte leds[] = {2, 3, 4, 5, 6};
byte modo = 0;
unsigned int passo = 0;
unsigned long ultimoPasso = 0;
unsigned long inicioModo = 0;

void sequencial(unsigned int etapa) {
  for (byte i = 0; i < 5; i++) {
    digitalWrite(leds[i], i == etapa % 5 ? HIGH : LOW);
  }
}

void piscarTodos(unsigned int etapa) {
  for (byte i = 0; i < 5; i++) {
    digitalWrite(leds[i], etapa % 2 == 0 ? HIGH : LOW);
  }
}

void alternados(unsigned int etapa) {
  for (byte i = 0; i < 5; i++) {
    digitalWrite(leds[i], (i + etapa) % 2 == 0 ? HIGH : LOW);
  }
}

void setup() {
  for (byte i = 0; i < 5; i++) {
    pinMode(leds[i], OUTPUT);
  }
}

void loop() {
  unsigned long agora = millis();
  if (agora - inicioModo >= 5000UL) {
    inicioModo = agora;
    modo = (modo + 1) % 3;
    passo = 0;
  }
  if (agora - ultimoPasso < 200UL) return;
  ultimoPasso = agora;
  switch (modo) {
    case 0: sequencial(passo); break;
    case 1: piscarTodos(passo); break;
    case 2: alternados(passo); break;
  }
  passo++;
}
'''

MOTOR_PWM = '''// Modelo didático: ponte H com PWM D9, IN1 D7 e IN2 D8.
// A0 ajusta a potência de comando; botão D2 para GND inverte o sentido.
// Em hardware, motor exige driver e alimentação próprios.
void setup() {
  pinMode(9, OUTPUT);
  pinMode(7, OUTPUT);
  pinMode(8, OUTPUT);
  pinMode(2, INPUT_PULLUP);
}

void loop() {
  bool frente = digitalRead(2) == HIGH;
  digitalWrite(7, frente ? HIGH : LOW);
  digitalWrite(8, frente ? LOW : HIGH);
  analogWrite(9, analogRead(A0) / 4);
}
'''

RELAYS = '''// Saídas de comando ativas em HIGH, não bobinas ligadas diretamente à MCU.
// Painel do simulador: Relés (4), pinos D2–D5, Ativo em LOW desmarcado.
void setup() {
  for (byte pin = 2; pin <= 5; pin++) pinMode(pin, OUTPUT);
}

void loop() {
  byte ativo = (millis() / 500) % 4;
  for (byte i = 0; i < 4; i++) digitalWrite(i + 2, i == ativo ? HIGH : LOW);
}
'''

SEVEN_SEGMENT = '''// Painel: Display 7 segmentos; a–g em D2–D8, ativo HIGH.
const byte numeros[] = {0x3f, 0x06, 0x5b, 0x4f, 0x66, 0x6d, 0x7d, 0x07, 0x7f, 0x6f};

void setup() {
  for (byte pin = 2; pin <= 8; pin++) pinMode(pin, OUTPUT);
}

void loop() {
  byte segmentos = numeros[(millis() / 1000) % 10];
  for (byte i = 0; i < 7; i++) digitalWrite(i + 2, segmentos & (1 << i) ? HIGH : LOW);
}
'''
