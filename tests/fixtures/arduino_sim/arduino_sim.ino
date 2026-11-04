// Emulation regression fixture. Does not require or drive physical hardware in tests.
volatile byte events = 0;

void inputChanged() {
  events++;
}

void setup() {
  Serial.begin(115200);
  pinMode(2, INPUT_PULLUP);
  pinMode(13, OUTPUT);
  pinMode(3, OUTPUT);
  pinMode(9, OUTPUT);
  attachInterrupt(digitalPinToInterrupt(2), inputChanged, CHANGE);
}

void loop() {
  digitalWrite(13, digitalRead(2) == LOW ? HIGH : LOW);
  digitalWrite(3, millis() % 20 < 10 ? HIGH : LOW);
  analogWrite(9, analogRead(A0) / 4);
  while (Serial.available()) {
    Serial.write(Serial.read());
  }
  static unsigned long previous = 0;
  if (millis() - previous >= 20) {
    previous = millis();
    Serial.print("A=");
    Serial.print(analogRead(A0));
    Serial.print(" E=");
    Serial.println(events);
  }
}
