"""Offline engineering utilities and reproducible embedded project starters."""
from __future__ import annotations

import math
from pathlib import Path

EMBEDDED_KINDS = ("arduino", "esp32", "stm32", "micropython", "electronics")


def positive(value):
    number = float(str(value).replace(",", "."))
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Use valores positivos e finitos, nas unidades indicadas.")
    return number


def calculate(kind, first, second):
    a, b = positive(first), positive(second)
    if kind == "ohm":
        return f"Corrente: {a / b:.6g} A | Potência no resistor: {a * a / b:.6g} W"
    if kind == "led":
        # first is the voltage across the resistor, second is LED current in mA.
        current = b / 1000
        return f"Resistência: {a / current:.6g} Ω | Dissipação: {a * current:.6g} W"
    if kind == "rc":
        tau = a * b * 1e-6
        return f"Constante de tempo: {tau:.6g} s | Corte RC: {1 / (2 * math.pi * tau):.6g} Hz"
    if kind == "power":
        return f"Potência DC: {a * b:.6g} W | Energia em 1 h: {a * b / 1000:.6g} kWh"
    raise ValueError("Cálculo desconhecido.")


def detect_embedded(workspace):
    root = Path(workspace)
    if (root / "platformio.ini").is_file():
        return "platformio"
    if (root / "sketch.yaml").is_file() or (root / "firmware/firmware.ino").is_file() or next(root.glob("*.ino"), None):
        return "arduino"
    if (root / "boot.py").is_file():
        return "micropython"
    if (root / "hardware" / "bom.csv").is_file() or any(next(root.glob(pattern), None) for pattern in ("*.mpcb.json", "hardware/*.mpcb.json", "*.kicad_pro", "*.kicad_pcb", "*.kicad_sch")):
        return "electronics"
    return ""


def tool_arguments(backend, action, *, fqbn="arduino:avr:uno", port="", baud="115200", environment=""):
    """Return argv, never shell text. Upload on Arduino compiles first."""
    if backend == "native":
        import sys
        if action not in {"install", "status", "boards", "build", "upload", "monitor"}:
            raise ValueError("Ação nativa desconhecida.")
        if action in {"upload", "monitor"} and not port.strip():
            raise ValueError("Informe explicitamente a porta serial.")
        args = [sys.executable, "-u", str(Path(__file__).with_name("native_firmware.py")), action,
                "--board", fqbn.strip(), "--environment", environment.strip()]
        if action in {"upload", "monitor"}:
            args += ["--port", port.strip()]
        if action == "monitor":
            value = positive(baud)
            if not value.is_integer() or not 300 <= value <= 2000000:
                raise ValueError("Baud deve ser inteiro de 300 a 2000000.")
            args += ["--baud", str(int(value))]
        return args
    if action not in {"boards", "build", "upload", "monitor"}:
        raise ValueError("Ação desconhecida.")
    if backend == "platformio":
        if action == "boards":
            return ["pio", "device", "list"]
        if action == "build":
            return ["pio", "run"]
        if action == "upload":
            if not port.strip():
                raise ValueError("Informe explicitamente a porta de gravação.")
            return ["pio", "run", "--target", "upload", "--upload-port", port.strip()]
        if not port.strip():
            raise ValueError("Informe a porta serial.")
        return ["pio", "device", "monitor", "--port", port.strip(), "--baud", str(int(positive(baud))) ]
    if backend != "arduino":
        raise ValueError("Selecione Arduino CLI ou PlatformIO.")
    if action == "boards":
        return ["arduino-cli", "board", "list"]
    if action == "monitor":
        if not port.strip():
            raise ValueError("Informe a porta serial.")
        return ["arduino-cli", "monitor", "--port", port.strip(), "--config", f"baudrate={int(positive(baud))}"]
    if not fqbn.strip():
        raise ValueError("Informe o FQBN da placa.")
    args = ["arduino-cli", "compile", "--fqbn", fqbn.strip()]
    if action == "upload":
        if not port.strip():
            raise ValueError("Informe explicitamente a porta de gravação.")
        args += ["--upload", "--port", port.strip()]
    return args + ["."]


BLINK = '''// LED de diagnóstico; não aciona cargas externas.
unsigned long previousTick = 0;
bool ledState = false;
void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}
void loop() {
  const unsigned long now = millis();
  if (now - previousTick >= 500UL) {
    previousTick = now;
    ledState = !ledState;
    digitalWrite(LED_BUILTIN, ledState);
  }
}
'''


def create_embedded_files(root, kind):
    root = Path(root)
    files = {
        "hardware/architecture.json": '{\n  "schema_version": 1,\n  "rails": [],\n  "blocks": [],\n  "interfaces": []\n}\n',
        "tests/functional-tests.csv": "Test,Requirement,Min,Max,Measured,Unit,Evidence\n",
        "hardware/bom.csv": "Reference,Quantity,Value,PartNumber,Footprint,VoltageRating,Notes\n",
        "hardware/pinout.csv": "Signal,MCU_Pin,Direction,Voltage,Connector,Notes\n",
        "docs/engineering.md": "# Dossiê de engenharia\n\n"
        "## Requisitos\nAplicação, alimentação, consumo, ambiente, interfaces e critérios de aceitação.\n\n"
        "## Projeto\nDiagrama de blocos, orçamento de potência, tolerâncias, térmica e modos de falha.\n"
        "Registrar datasheets e revisões, pinagem, níveis lógicos, BOM e alternativas.\n\n"
        "## Validação\nInspeção sem energia; fonte limitada em corrente; medir tensões antes de conectar a MCU.\n"
        "Registrar instrumento, procedimento, valor esperado, medido e resultado.\n"
        "Para rede elétrica/industrial: isolamento, proteção, EMC, aterramento e revisão por responsável habilitado.\n"
        "Firmware de exemplo não implementa funções de segurança certificadas.\n",
        "tests/bench-plan.csv": "Test,Setup,Expected,Measured,Result\nPower rails,Current-limited supply,Within design tolerance,,\n",
        ".gitignore": ".pio/\nbuild/\n__pycache__/\n.merotec_system_ai/\n",
    }
    if kind == "electronics":
        import json
        from modules.native_pcb import new_design
        files["hardware/design.mpcb.json"] = json.dumps(new_design(), ensure_ascii=False, indent=2) + "\n"
    if kind == "arduino":
        # A fixed ASCII sketch directory also supports project names with spaces.
        files["firmware/firmware.ino"] = BLINK
        files["firmware/sketch.yaml"] = "default_fqbn: arduino:avr:uno\n"
        files["firmware/merotec-firmware.json"] = '{"version": 1, "board": "uno", "source_dir": ".", "include_dir": "include"}\n'
    elif kind in {"esp32", "stm32"}:
        platform, board = ("espressif32", "esp32dev") if kind == "esp32" else ("ststm32", "nucleo_f401re")
        files["platformio.ini"] = f"[env:{board}]\nplatform = {platform}\nboard = {board}\nframework = arduino\nmonitor_speed = 115200\n"
        files["src/main.cpp"] = "#include <Arduino.h>\n" + ("#ifndef LED_BUILTIN\n#define LED_BUILTIN 2\n#endif\n" if kind == "esp32" else "") + BLINK
    elif kind == "micropython":
        files["boot.py"] = "# Inicialização MicroPython; configurar conforme a placa.\n"
        files["main.py"] = "import time\n\nwhile True:\n    print('Merotec: diagnóstico ativo')\n    time.sleep_ms(1000)\n"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    with (root / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("\n## Bancada eletrônica\nAbra Engenharia > Bancada eletrônica na IDE.\n"
                     "Arduino: selecione a pasta firmware para compilar/gravar. ESP32/STM32: use PlatformIO na raiz.\n"
                     "Instale a ferramenta e o core da placa separadamente; confira placa, porta e pinagem.\n"
                     "ESP32 usa GPIO 2 como exemplo: adapte ao seu hardware. MicroPython exige firmware e transferência compatíveis com a placa.\n"
                     "BOM, pinagem e plano de ensaios são modelos a preencher, não um circuito validado.\n")
        handle.write("\n## Diagnóstico PCB e arquitetura\n"
                     "Na bancada, abra PCB e arquitetura. Preencha hardware/architecture.json e tests/functional-tests.csv.\n"
                     "Arquitetura v1: rails (id, voltage_v, max_current_ma); blocks (id, rail, current_ma, supply_min_v, supply_max_v, logic_voltage_v, max_input_v); interfaces (from, to, level_shifter boolean opcional).\n"
                     "Uma referência por linha na BOM. Pinout Direction: input/output/bidirectional/power/ground; Voltage numérico em V.\n"
                     "Testes funcionais: limites Min/Max inclusivos, Measured na mesma Unit e Evidence identificando o registro de medição.\n"
                     "Campos não preenchidos são pendentes; os diagnósticos não substituem medições e revisão física.\n")
