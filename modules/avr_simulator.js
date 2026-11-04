// Merotec integration. AVR8js is embedded separately under its MIT license.
globalThis.simulator = null;
globalThis.createSimulator = function(bytes) {
  const A = AVR8;
  const flash = new Uint16Array(16384);
  new Uint8Array(flash.buffer).set(bytes);
  const cpu = new A.CPU(flash, 2048);
  const instruction = A.avrInstruction;
  const timers = [new A.AVRTimer(cpu, A.timer0Config), new A.AVRTimer(cpu, A.timer1Config), new A.AVRTimer(cpu, A.timer2Config)];
  const b = new A.AVRIOPort(cpu, A.portBConfig);
  const c = new A.AVRIOPort(cpu, A.portCConfig);
  const d = new A.AVRIOPort(cpu, A.portDConfig);
  const adc = new A.AVRADC(cpu, A.adcConfig);
  const uart = new A.AVRUSART(cpu, A.usart0Config, 16000000);
  const eeprom = new A.AVREEPROM(cpu, new A.EEPROMMemoryBackend(1024));
  let serial = [], rx = [], dropped = 0, sampleCycle = 0;
  const pins = [];
  for (let pin = 0; pin < 20; pin++) {
    const port = pin < 8 ? d : pin < 14 ? b : c;
    const bit = pin < 8 ? pin : pin < 14 ? pin - 8 : pin - 14;
    pins.push({port, bit, external: null, highCycles: 0, lastCycle: 0, high: false});
  }
  function updatePin(p) {
    p.highCycles += p.high ? cpu.cycles - p.lastCycle : 0;
    p.lastCycle = cpu.cycles;
    const mode = p.port.pinState(p.bit);
    if (mode >= 2) p.port.setPin(p.bit, p.external === null ? mode === 3 : p.external);
    p.high = mode === 1;
  }
  for (const port of [b, c, d]) {
    port.addListener(() => { for (const p of pins) if (p.port === port) updatePin(p); });
  }
  uart.onByteTransmit = value => { if (serial.length < 8192) serial.push(value); else dropped++; };
  for (let channel = 0; channel < 8; channel++) adc.channelValues[channel] = 0;
  const sim = {
    advance(cycles) {
      const end = cpu.cycles + Math.min(500000, Math.max(0, cycles));
      while (cpu.cycles < end) {
        if (cpu.pc >= flash.length) throw new Error('PC fora da Flash ATmega328P');
        if (rx.length && !uart.rxBusy && uart.rxEnable) uart.writeByte(rx.shift());
        instruction(cpu);
        cpu.tick();
      }
      return this.snapshot();
    },
    input(pin, value) {
      if (!Number.isInteger(pin) || pin < 0 || pin >= 20) throw new Error('Pino inválido');
      const p = pins[pin];
      p.external = value === null ? null : !!value;
      updatePin(p);
    },
    analog(channel, value) {
      if (!Number.isInteger(channel) || channel < 0 || channel > 5) throw new Error('ADC inválido');
      adc.channelValues[channel] = Math.min(1023, Math.max(0, value)) * 5 / 1023;
    },
    receive(bytes) {
      if (rx.length + bytes.length > 4096) throw new Error('Fila serial cheia');
      rx.push(...bytes);
    },
    snapshot() {
      const elapsed = cpu.cycles - sampleCycle;
      const states = pins.map((p, pin) => {
        updatePin(p);
        const mode = p.port.pinState(p.bit);
        const level = !!(cpu.data[p.port.portConfig.PIN] & (1 << p.bit));
        const duty = elapsed ? p.highCycles / elapsed : (mode === 1 ? 1 : 0);
        const conflict = mode < 2 && p.external !== null && p.external !== (mode === 1);
        p.highCycles = 0;
        return {pin, mode: mode < 2 ? 'OUTPUT' : mode === 3 ? 'INPUT_PULLUP' : 'INPUT', level, duty, conflict};
      });
      const output = serial; serial = [];
      const lost = dropped; dropped = 0;
      sampleCycle = cpu.cycles;
      return {cycles: cpu.cycles, time_ms: cpu.cycles / 16000, pc: cpu.pc, pins: states, serial: output, serial_dropped: lost};
    }
  };
  simulator = sim;
  return sim.snapshot();
};
