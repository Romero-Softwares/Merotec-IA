"""Evidence-based PCB diagnostics. Never powers or programs hardware."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import shutil
import sys
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.process_runtime import subprocess
from datetime import datetime, timezone


REPORT_DIR = ".merotec_system_ai/pcb_diagnostics"
SKIP_DIRS = {".git", ".pio", "venv", ".venv", "node_modules", "__pycache__", ".merotec_system_ai"}


def number(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("número finito não negativo esperado")
    return result


def diagnose(workspace, *, kicad=False, timeout=120):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise ValueError("Workspace inexistente.")
    findings = []

    def add(status, check, detail):
        findings.append({"status": status, "check": check, "detail": detail})

    def read_csv(relative, required):
        path = root / relative
        try:
            path.resolve().relative_to(root)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                if not set(required) <= set(reader.fieldnames or ()):
                    raise ValueError("colunas obrigatórias: " + ", ".join(required))
                rows = [row for row in reader if any(str(v or "").strip() for v in row.values())]
            if not rows:
                add("pending", relative, "Arquivo sem registros preenchidos.")
            return rows
        except FileNotFoundError:
            add("pending", relative, "Arquivo ausente.")
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            add("fail", relative, str(exc))
        return []

    seen = set()
    for index, row in enumerate(read_csv("hardware/bom.csv", ("Reference", "Quantity", "Value", "Footprint")), 2):
        label = f"BOM linha {index}"
        try:
            ref = (row["Reference"] or "").strip()
            if not ref or ref in seen:
                raise ValueError("Referência ausente ou duplicada; use uma referência por linha.")
            seen.add(ref)
            quantity = number(row["Quantity"])
            if quantity < 1 or not quantity.is_integer():
                raise ValueError("Quantidade deve ser um inteiro positivo.")
            if not (row["Value"] or "").strip() or not (row["Footprint"] or "").strip():
                raise ValueError("Valor ou footprint não preenchido.")
            add("pass", label, f"{ref}: campos básicos consistentes; datasheet e footprint físico não verificados.")
        except (ValueError, TypeError) as exc:
            add("fail", label, str(exc))

    pins = set()
    for index, row in enumerate(read_csv("hardware/pinout.csv", ("Signal", "MCU_Pin", "Direction", "Voltage")), 2):
        try:
            pin = (row["MCU_Pin"] or "").strip()
            if not pin or pin in pins or not (row["Signal"] or "").strip():
                raise ValueError("Sinal/pino ausente ou pino alocado mais de uma vez.")
            pins.add(pin)
            if (row["Direction"] or "").strip().lower() not in {"input", "output", "bidirectional", "power", "ground"}:
                raise ValueError("Direction: input, output, bidirectional, power ou ground.")
            number(row["Voltage"])
            add("pass", f"Pinagem linha {index}", "Alocação única e tensão declarada; não confrontada com datasheet.")
        except (ValueError, TypeError) as exc:
            add("fail", f"Pinagem linha {index}", str(exc))

    architecture_path = root / "hardware/architecture.json"
    try:
        architecture_path.resolve().relative_to(root)
        data = json.loads(architecture_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("architecture.json exige schema_version: 1.")
        rails, blocks = {}, {}
        for key in ("rails", "blocks", "interfaces"):
            if not isinstance(data.get(key), list):
                raise ValueError(f"{key} deve ser uma lista.")
        for item in data["rails"]:
            if not isinstance(item, dict):
                raise ValueError("Rail deve ser um objeto.")
            ident = item.get("id")
            if not isinstance(ident, str) or not ident.strip() or ident in rails:
                raise ValueError("Identificador de rail ausente ou duplicado.")
            rails[ident] = (number(item["voltage_v"]), number(item["max_current_ma"]))
        loads = {key: 0.0 for key in rails}
        for item in data["blocks"]:
            if not isinstance(item, dict):
                raise ValueError("Bloco deve ser um objeto.")
            ident = item.get("id")
            if not isinstance(ident, str) or not ident.strip() or ident in blocks:
                raise ValueError("Identificador de bloco ausente ou duplicado.")
            rail = item["rail"]
            if rail not in rails:
                raise ValueError(f"Bloco {ident}: rail desconhecido.")
            low, high = number(item["supply_min_v"]), number(item["supply_max_v"])
            if low > high:
                raise ValueError(f"Bloco {ident}: intervalo de alimentação invertido.")
            loads[rail] += number(item["current_ma"])
            blocks[ident] = (number(item["logic_voltage_v"]), number(item["max_input_v"]))
            add("pass" if low <= rails[rail][0] <= high else "fail", f"Alimentação {ident}",
                f"Rail {rail}: {rails[rail][0]:g} V; faixa declarada {low:g}–{high:g} V.")
        for rail, current in loads.items():
            add("pass" if current <= rails[rail][1] else "fail", f"Orçamento {rail}",
                f"Carga declarada {current:g} mA / capacidade {rails[rail][1]:g} mA; sem cálculo de transientes ou térmica.")
        for item in data["interfaces"]:
            if not isinstance(item, dict):
                raise ValueError("Interface deve ser um objeto.")
            source, target = item["from"], item["to"]
            if source not in blocks or target not in blocks:
                raise ValueError("Interface referencia bloco desconhecido.")
            shifted = item.get("level_shifter", False)
            if not isinstance(shifted, bool):
                raise ValueError("level_shifter deve ser true ou false.")
            status = "pending" if shifted else ("pass" if blocks[source][0] <= blocks[target][1] else "fail")
            add(status, f"Interface {source} → {target}", "Conversor declarado: validar componente e tensões." if shifted else
                f"Saída {blocks[source][0]:g} V / máximo de entrada {blocks[target][1]:g} V; limiares VIH/VIL não analisados.")
        if not rails or not blocks:
            add("pending", "Arquitetura", "Preencha rails e blocks; arquitetura vazia não valida o dispositivo.")
        elif not data["interfaces"]:
            add("pending", "Interfaces", "Nenhuma conexão entre blocos declarada.")
    except FileNotFoundError:
        add("pending", "Arquitetura", "hardware/architecture.json ausente.")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        add("fail", "Arquitetura", f"Dados inválidos: {exc}")

    for index, row in enumerate(read_csv("tests/functional-tests.csv", ("Test", "Requirement", "Min", "Max", "Measured", "Unit", "Evidence")), 2):
        check = f"Ensaio linha {index}: {row.get('Test', '')}"
        try:
            if any(not (row.get(key) or "").strip() for key in ("Test", "Requirement", "Unit")):
                raise ValueError("Preencha Test, Requirement e Unit.")
            low, high = float(row["Min"]), float(row["Max"])
            if not all(math.isfinite(v) for v in (low, high)) or low > high:
                raise ValueError("Limites inválidos ou invertidos.")
            if not (row["Measured"] or "").strip() or not (row["Evidence"] or "").strip():
                add("pending", check, "Medição ou evidência não registrada; ensaio não executado pela IDE.")
                continue
            measured = float(row["Measured"])
            if not math.isfinite(measured):
                raise ValueError("Medição deve ser finita.")
            add("pass" if low <= measured <= high else "fail", check,
                f"{measured:g} {row['Unit']}; limites [{low:g}, {high:g}]; evidência declarada: {row['Evidence']}. Não verificada fisicamente.")
        except (ValueError, TypeError) as exc:
            add("fail", check, str(exc))

    sources, native_sources = [], []
    for folder, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in SKIP_DIRS and not (Path(folder) / d).is_symlink())
        for name in sorted(names):
            path = Path(folder) / name
            if name.endswith(".mpcb.json"):
                try:
                    path.resolve().relative_to(root)
                    native_sources.append(path)
                except ValueError:
                    add("fail", "PCB nativo", f"Link fora do workspace: {path.relative_to(root)}")
            if path.suffix in {".kicad_pcb", ".kicad_sch"}:
                try:
                    path.resolve().relative_to(root)
                    sources.append(path)
                except ValueError:
                    add("fail", "Arquivo KiCad", f"Link fora do workspace: {path.relative_to(root)}")
    for path in native_sources:
        try:
            if __package__:
                from modules.native_pcb import load, checks
            else:
                from native_pcb import load, checks
            findings_native = checks(load(path))
            if not findings_native:
                add("pass", f"ERC/DRC nativo: {path.relative_to(root)}", "Sem violações nas regras nativas de camada única SMD; não certifica hardware.")
            for finding in findings_native:
                add("pending" if finding["code"] == "EMPTY" else "fail", f"{path.relative_to(root)}: {finding['code']}", finding["detail"])
        except (ValueError, OSError) as exc:
            add("fail", f"PCB nativo: {path.relative_to(root)}", str(exc))
    if not sources and not native_sources:
        add("pending", "KiCad ERC/DRC", "Nenhum .kicad_pcb ou .kicad_sch encontrado.")
    executable = shutil.which("kicad-cli") if kicad else None
    for index, source in enumerate(sources):
        check = f"{'DRC' if source.suffix == '.kicad_pcb' else 'ERC'}: {source.relative_to(root)}"
        if not executable:
            add("pending", check, "kicad-cli ausente no PATH." if kicad else "Verificação KiCad não solicitada.")
            continue
        output = safe_report_dir(root) / f"kicad-{index}.rpt"
        args = [executable, "pcb" if source.suffix == ".kicad_pcb" else "sch", "drc" if source.suffix == ".kicad_pcb" else "erc",
                "--exit-code-violations", "--output", str(output), str(source)]
        try:
            if output.exists():
                output.unlink()
            result = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            status = "pass" if result.returncode == 0 and output.is_file() else "fail"
            add(status, check, f"Exit code {result.returncode}; relatório {output.relative_to(root)}. " + (result.stdout + result.stderr)[-3000:])
        except subprocess.TimeoutExpired:
            add("fail", check, f"Tempo limite de {timeout}s excedido.")
        except OSError as exc:
            add("fail", check, f"Falha ao executar KiCad: {exc}")
    counts = {status: sum(f["status"] == status for f in findings) for status in ("pass", "fail", "pending")}
    return {"schema_version": 1, "workspace": str(root), "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "fail" if counts["fail"] else ("pending" if counts["pending"] else "pass"),
            "counts": counts, "findings": findings}


def safe_report_dir(root):
    root = Path(root).resolve()
    path = root / REPORT_DIR
    path.resolve().relative_to(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_report(report):
    lines = ["Diagnóstico PCB e arquitetura", f"Estado: {report['status']}", f"Workspace: {report['workspace']}",
             f"Data UTC: {report['created_at']}", f"Contagens: {report['counts']}",
             "PASS confirma somente a regra declarada. Não certifica hardware nem substitui ensaios físicos.", ""]
    lines.extend(f"[{f['status'].upper()}] {f['check']}: {f['detail']}" for f in report["findings"])
    return "\n".join(lines) + "\n"


def save_report(root, report):
    folder = safe_report_dir(root)
    for name, content in (("report.json", json.dumps(report, ensure_ascii=False, indent=2)), ("report.txt", render_report(report))):
        path = folder / name
        path.resolve().relative_to(Path(root).resolve())
        path.write_text(content, encoding="utf-8")
    return folder / "report.txt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--kicad", action="store_true", help="Executar ERC/DRC com kicad-cli instalado")
    args = parser.parse_args()
    try:
        report = diagnose(args.workspace, kicad=args.kicad)
        path = save_report(args.workspace, report)
        print(render_report(report))
        print(f"Relatório: {path}")
        return {"pass": 0, "fail": 1, "pending": 2}[report["status"]]
    except (ValueError, OSError) as exc:
        print(f"Diagnóstico não concluído: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
