"""Python firmware build manager: AVR compilation, upload and serial without CLI/PIO."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.process_runtime import subprocess

if __package__:
    from modules.firmware_packages import DEFAULT_STORE, installed_paths, install_avr
else:
    from firmware_packages import DEFAULT_STORE, installed_paths, install_avr


BOARDS = {
    "uno": dict(mcu="atmega328p", variant="standard", macro="AVR_UNO", protocol="arduino", speed=115200, flash=32256, ram=2048),
    "nano": dict(mcu="atmega328p", variant="eightanaloginputs", macro="AVR_NANO", protocol="arduino", speed=115200, flash=30720, ram=2048),
    "nano-old": dict(mcu="atmega328p", variant="eightanaloginputs", macro="AVR_NANO", protocol="arduino", speed=57600, flash=30720, ram=2048),
    "mega2560": dict(mcu="atmega2560", variant="mega", macro="AVR_MEGA2560", protocol="wiring", speed=115200, flash=253952, ram=8192),
}
SOURCE_SUFFIXES = {".c", ".cpp", ".cc", ".cxx", ".S"}


def configuration(root, board="uno", environment=""):
    root = Path(root).resolve()
    aliases_fqbn = {"arduino:avr:uno": "uno", "arduino:avr:nano": "nano", "arduino:avr:nano:cpu=atmega328": "nano",
                    "arduino:avr:nano:cpu=atmega328old": "nano-old", "arduino:avr:mega": "mega2560", "arduino:avr:mega:cpu=atmega2560": "mega2560"}
    config = {"board": aliases_fqbn.get(board, board), "source_dir": ".", "include_dir": "include"}
    manifest = root / "merotec-firmware.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("version") != 1 or set(data) - {"version", "board", "source_dir", "include_dir"}:
            raise ValueError("Manifesto firmware v1 inválido. Campos: version, board, source_dir, include_dir.")
        config.update({k: v for k, v in data.items() if k != "version"})
    elif (root / "platformio.ini").is_file():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(root / "platformio.ini", encoding="utf-8-sig")
        if parser.has_section("platformio"):
            extra = set(dict(parser.items("platformio"))) - {"default_envs", "src_dir", "include_dir", "description"}
            if extra:
                raise ValueError("Configurações PIO globais ainda não suportadas: " + ", ".join(sorted(extra)))
        envs = [s[4:] for s in parser.sections() if s.startswith("env:")]
        selected = environment or parser.get("platformio", "default_envs", fallback="").strip()
        if not selected:
            if len(envs) != 1:
                raise ValueError("Selecione um ambiente explicitamente para este platformio.ini.")
            selected = envs[0]
        if selected not in envs:
            raise ValueError(f"Ambiente desconhecido: {selected}")
        values = dict(parser.items("env")) if parser.has_section("env") else {}
        values.update(parser.items("env:" + selected))
        unsupported = set(values) - {"platform", "board", "framework", "monitor_speed", "upload_port"}
        if unsupported:
            raise ValueError("Opções PIO ainda não suportadas nativamente: " + ", ".join(sorted(unsupported)))
        if values.get("platform") != "atmelavr" or values.get("framework") != "arduino":
            raise ValueError("Backend nativo atual: atmelavr + arduino. ESP32/STM32 ainda exigem SDK/backend compatível.")
        aliases = {"uno": "uno", "nanoatmega328": "nano-old", "nanoatmega328new": "nano", "megaatmega2560": "mega2560"}
        config["board"] = aliases.get(values.get("board"), values.get("board"))
        config["source_dir"] = parser.get("platformio", "src_dir", fallback="src")
        config["include_dir"] = parser.get("platformio", "include_dir", fallback="include")
    if not isinstance(config["board"], str) or config["board"] not in BOARDS:
        raise ValueError(f"Placa não suportada pelo backend nativo: {config['board']}. Use uno, nano, nano-old ou mega2560.")
    for key in ("source_dir", "include_dir"):
        if not isinstance(config[key], str):
            raise ValueError(f"{key} deve ser um caminho relativo.")
        path = (root / config[key]).resolve()
        path.relative_to(root)
        config[key] = path
    if not config["source_dir"].is_dir():
        raise ValueError("Pasta de fontes inexistente.")
    return config


def sources_in(folder, recursive=True):
    if not Path(folder).is_dir():
        return []
    iterator = Path(folder).rglob("*") if recursive else Path(folder).glob("*")
    ignored = {".merotec_system_ai", ".git", ".pio", "build", "examples", "extras", "tests", "test", "venv", ".venv", "node_modules"}
    return sorted(p for p in iterator if p.is_file() and p.suffix in SOURCE_SUFFIXES and not set(p.relative_to(folder).parts) & ignored)


def discover_libraries(texts, roots):
    candidates = []
    for root in roots:
        if Path(root).is_dir():
            for folder in sorted(Path(root).iterdir()):
                if folder.is_dir():
                    candidates.append(folder / "src" if (folder / "src").is_dir() else folder)
    chosen, pending = [], list(texts)
    includes = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]', re.MULTILINE)
    while pending:
        for header in includes.findall(pending.pop()):
            for candidate in candidates:
                target = (candidate / header).resolve()
                try:
                    target.relative_to(candidate.resolve())
                except ValueError:
                    continue
                if target.is_file():
                    if candidate not in chosen:
                        chosen.append(candidate)
                        pending.extend(p.read_text(encoding="utf-8", errors="replace") for p in candidate.rglob("*")
                                       if p.is_file() and p.suffix in SOURCE_SUFFIXES | {".h", ".hpp"})
                    break
    return chosen


def run(args, cwd, log=print):
    log("  " + subprocess.list2cmdline([str(a) for a in args]))
    # QProcess already supplies a pipe-backed terminal. Inherit stdout/stderr for streaming.
    result = subprocess.run([str(a) for a in args], cwd=cwd, timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"Comando falhou (código {result.returncode}): {Path(args[0]).name}")


def build(root, *, board="uno", environment="", store=DEFAULT_STORE, log=print):
    root = Path(root).resolve()
    config = configuration(root, board, environment)
    profile = BOARDS[config["board"]]
    tools = installed_paths(store)
    output = root / ".merotec_system_ai/firmware_build" / config["board"]
    output.resolve().relative_to(root)
    output.mkdir(parents=True, exist_ok=True)
    previous_report = output / "build.json"
    previous_report.resolve().relative_to(root)
    if previous_report.exists():
        previous_report.unlink()
    suffix = ".exe" if os.name == "nt" else ""
    def tool(name):
        path = tools["bin"] / (name + suffix)
        if not path.is_file():
            raise ValueError(f"Ferramenta ausente: {path}")
        return path
    core = tools["core"]
    variant = tools["platform"] / "variants" / profile["variant"]
    user_sources = (sources_in(root, False) + sources_in(root / "src")) if config["source_dir"] == root else sources_in(config["source_dir"])
    # Root Arduino tabs only; nested .ino projects are separate sketches.
    sketches = sorted(config["source_dir"].glob("*.ino"))
    primary = config["source_dir"] / (config["source_dir"].name + ".ino")
    if primary in sketches:
        sketches.remove(primary)
        sketches.insert(0, primary)
    if not user_sources and not sketches:
        raise ValueError("Nenhum fonte C/C++ ou sketch .ino encontrado.")
    texts = [p.read_text(encoding="utf-8-sig", errors="strict") for p in user_sources + sketches]
    header_roots = [config["source_dir"]] if config["source_dir"] != root else [root, root / "src"]
    for header_root in header_roots:
        if header_root.is_dir():
            headers = header_root.glob("*") if header_root == root else header_root.rglob("*")
            texts.extend(p.read_text(encoding="utf-8", errors="replace") for p in headers if p.is_file() and p.suffix in {".h", ".hpp"})
    texts += [p.read_text(encoding="utf-8", errors="replace") for p in config["include_dir"].rglob("*.h")] if config["include_dir"].is_dir() else []
    if sketches:
        generated = output / "sketch.cpp"
        generated.resolve().relative_to(root)
        content = '#include <Arduino.h>\n'
        for source in sketches:
            # JSON string quoting is also valid for C++ #line path strings.
            content += f'\n#line 1 {json.dumps(source.as_posix())}\n' + source.read_text(encoding="utf-8-sig") + "\n"
        generated.write_text(content, encoding="utf-8")
        user_sources.append(generated)
        log("Sketch convertido em C++. Funções usadas antes da definição exigem protótipos explícitos.")
    libraries = discover_libraries(texts, [root / "libraries", root / "lib", tools["platform"] / "libraries"])
    include_paths = [core, variant, config["source_dir"], config["include_dir"], *libraries]
    includes = [flag for path in include_paths if path.is_dir() for flag in ("-I", str(path))]
    common = [f"-mmcu={profile['mcu']}", "-DF_CPU=16000000L", "-DARDUINO=10819", "-DARDUINO_ARCH_AVR", f"-DARDUINO_{profile['macro']}"]
    groups = [("core", sources_in(core, False) + sources_in(variant, False)),
              ("project", user_sources + [source for library in libraries for source in sources_in(library)])]
    objects = {"core": [], "project": []}
    # No hidden cache: every build compiles current sources and headers.
    for group, files in groups:
        for source in files:
            obj = output / (group + "-" + hashlib.sha256(str(source).encode()).hexdigest()[:20] + ".o")
            obj.resolve().relative_to(root)
            if source.suffix in {".cpp", ".cc", ".cxx"}:
                compiler = tool("avr-g++")
                flags = ["-std=gnu++11", "-fpermissive", "-fno-exceptions", "-fno-threadsafe-statics"]
            else:
                compiler = tool("avr-gcc")
                flags = ["-x", "assembler-with-cpp"] if source.suffix == ".S" else ["-std=gnu11"]
            run([compiler, "-c", "-Os", "-g", "-ffunction-sections", "-fdata-sections", "-flto", *flags, *common, *includes, str(source), "-o", str(obj)], root, log)
            objects[group].append(obj)
    archive = output / "core.a"
    archive.resolve().relative_to(root)
    if archive.exists():
        archive.unlink()
    run([tool("avr-gcc-ar"), "rcs", archive, *objects["core"]], root, log)
    elf, hex_path = output / "firmware.elf", output / "firmware.hex"
    for path in (elf, hex_path):
        path.resolve().relative_to(root)
    run([tool("avr-gcc"), "-Os", "-flto", "-fuse-linker-plugin", "-Wl,--gc-sections", f"-mmcu={profile['mcu']}", "-o", elf, *objects["project"], archive, "-lm"], root, log)
    run([tool("avr-objcopy"), "-O", "ihex", "-R", ".eeprom", elf, hex_path], root, log)
    result = subprocess.run([str(tool("avr-size")), "-A", str(elf)], cwd=root, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Não foi possível medir o uso de memória.")
    sections = {name: int(size) for name, size in re.findall(r"^(\.[\w.]+)\s+(\d+)\s+", result.stdout, re.MULTILINE)}
    flash = sum(sections.get(name, 0) for name in (".text", ".data", ".bootloader"))
    ram = sum(sections.get(name, 0) for name in (".data", ".bss", ".noinit"))
    if not sections or flash > profile["flash"] or ram > profile["ram"]:
        raise ValueError(f"Firmware excede memória ou leitura inválida: Flash {flash}/{profile['flash']}; RAM estática {ram}/{profile['ram']}.")
    report = {"board": config["board"], "hex": str(hex_path), "flash_bytes": flash, "static_ram_bytes": ram,
              "libraries": [str(p) for p in libraries], "core": "Arduino AVR 1.8.6", "manager": "Merotec Python"}
    report_path = output / "build.json"
    report_path.resolve().relative_to(root)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"Compilação concluída. Flash: {flash}/{profile['flash']} bytes; RAM estática: {ram}/{profile['ram']} bytes.")
    log(f"HEX: {hex_path}")
    return report


def upload(root, port, *, board="uno", environment="", store=DEFAULT_STORE, log=print):
    if not port or not port.strip():
        raise ValueError("Porta explícita obrigatória para gravação.")
    # A failed build always stops before the programmer is launched.
    report = build(root, board=board, environment=environment, store=store, log=log)
    tools = installed_paths(store)
    profile = BOARDS[report["board"]]
    run([tools["avrdude"], "-C", tools["avrdude_conf"], "-p", profile["mcu"], "-c", profile["protocol"],
         "-P", port.strip(), "-b", str(profile["speed"]), "-D", "-U", f"flash:w:{report['hex']}:i"], Path(root), log)
    log("Gravação e verificação avrdude concluídas.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "status", "boards", "build", "upload", "monitor"])
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--board", default="uno")
    parser.add_argument("--environment", default="")
    parser.add_argument("--port", default="")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()
    try:
        if args.action == "install":
            install_avr(args.store)
        elif args.action == "status":
            print(json.dumps({k: str(v) for k, v in installed_paths(args.store).items()}, indent=2))
        elif args.action in {"boards", "monitor"}:
            if __package__:
                from modules.native_serial import ports, monitor
            else:
                from native_serial import ports, monitor
            if args.action == "boards":
                print("Portas seriais (modelo da placa não inferido):")
                print("\n".join(ports()) or "Nenhuma porta encontrada.")
                print("Perfis nativos: " + ", ".join(BOARDS))
            else:
                monitor(args.port, args.baud)
        elif args.action == "upload":
            upload(args.project, args.port, board=args.board, environment=args.environment, store=args.store)
        else:
            build(args.project, board=args.board, environment=args.environment, store=args.store)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, StopIteration, configparser.Error, subprocess.TimeoutExpired) as exc:
        print(f"Firmware nativo: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
