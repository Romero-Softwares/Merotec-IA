# AVR simulator regression firmware

`firmware.hex` is built from the adjacent `arduino_sim.ino` for Uno ATmega328P
at 16 MHz using Merotec's native builder, Arduino AVR Core 1.8.6 and the official
AVR GCC 7.3.0 toolchain. Tests use the fixture without downloading a compiler.

Rebuild from the IDE root:

```powershell
venv\Scripts\python.exe -m modules.native_firmware build --project tests/fixtures/arduino_sim --board uno
```

Copy the generated `.merotec_system_ai/firmware_build/uno/firmware.hex` here.
The fixture covers INPUT_PULLUP, external CHANGE interrupts, millis(), ADC A0,
Timer1 PWM D9 and bidirectional UART. No physical ports are opened.

Linked core code: [ArduinoCore-avr 1.8.6](https://github.com/arduino/ArduinoCore-avr/tree/1.8.6),
copyright Arduino and contributors, under the LGPL terms in the respective source
headers. The complete application source is provided alongside this fixture for
rebuilding/relinking with a modified core. The emulator's separate MIT license is
in `modules/vendor/AVR8JS_LICENSE.txt`.
