"""Managed AVR compiler/core installation. No Arduino CLI or PlatformIO required."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

INDEX_URL = "https://downloads.arduino.cc/packages/package_index.json"
CORE_VERSION = "1.8.6"
DEFAULT_STORE = Path(__file__).resolve().parent.parent / ".merotec_system_ai/firmware_tools"


def download(url, target, checksum=None):
    if not url.startswith("https://"):
        raise ValueError("Pacote exige HTTPS.")
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=60) as response, Path(target).open("wb") as handle:
        if not response.url.startswith("https://"):
            raise ValueError("Redirecionamento sem HTTPS recusado.")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 250_000_000:
                raise ValueError("Pacote excede limite de download.")
            digest.update(chunk)
            handle.write(chunk)
    if checksum and checksum.upper() != "SHA-256:" + digest.hexdigest().upper():
        raise ValueError("Checksum SHA-256 do pacote não confere.")
    return digest.hexdigest()


def extract(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                name = entry.filename.replace("\\", "/")
                target = destination / name
                target.resolve().relative_to(destination)
                if ":" in name or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Link/caminho de pacote inválido.")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as package:
            package.extractall(destination, filter="data")


def host_matches(host):
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        return host in {"i686-mingw32", "x86_64-mingw32"}
    if system == "Linux":
        return host in ({"x86_64-linux-gnu", "x86_64-pc-linux-gnu"} if machine in {"amd64", "x86_64"} else {"aarch64-linux-gnu"})
    if system == "Darwin":
        return host in ({"arm64-apple-darwin"} if machine == "arm64" else {"x86_64-apple-darwin", "x86_64-apple-darwin14"})
    return False


def package_plan(index):
    package = next(p for p in index["packages"] if p["name"] == "arduino")
    core = next(p for p in package["platforms"] if p["architecture"] == "avr" and p["version"] == CORE_VERSION)
    plan = [("core", core)]
    for name in ("avr-gcc", "avrdude"):
        dependency = next(d for d in core["toolsDependencies"] if d["name"] == name)
        tool = next(t for t in package["tools"] if t["name"] == name and t["version"] == dependency["version"])
        try:
            system = next(s for s in tool["systems"] if host_matches(s["host"]))
        except StopIteration as exc:
            raise ValueError(f"Pacote {name} indisponível para este sistema.") from exc
        plan.append((name, system))
    return plan


def find_one(root, pattern):
    matches = sorted(Path(root).rglob(pattern))
    if not matches:
        raise ValueError(f"Instalação incompleta: {pattern} ausente.")
    return matches[0]


def installed_paths(store=DEFAULT_STORE):
    root = Path(store) / f"avr-{CORE_VERSION}"
    suffix = ".exe" if os.name == "nt" else ""
    return {"core": find_one(root / "core", "Arduino.h").parent,
            "platform": find_one(root / "core", "boards.txt").parent,
            "bin": find_one(root / "avr-gcc", "avr-g++" + suffix).parent,
            "avrdude": find_one(root / "avrdude", "avrdude" + suffix),
            "avrdude_conf": find_one(root / "avrdude", "avrdude.conf")}


def install_avr(store=DEFAULT_STORE, log=print):
    store = Path(store).resolve()
    store.mkdir(parents=True, exist_ok=True)
    target = store / f"avr-{CORE_VERSION}"
    if target.exists():
        installed_paths(store)
        log("Pacotes AVR já disponíveis na IDE.")
        return installed_paths(store)
    with tempfile.TemporaryDirectory(prefix="install-", dir=store) as staging:
        staging = Path(staging)
        index_path = staging / "index.json"
        log("Consultando índice oficial Arduino…")
        download(INDEX_URL, index_path)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        receipt = []
        unpacked = staging / "packages"
        for name, entry in package_plan(index):
            log(f"Baixando {name}…")
            archive = staging / (name + ".archive")
            sha = download(entry["url"], archive, entry["checksum"])
            extract(archive, unpacked / name)
            receipt.append({"name": name, "url": entry["url"], "sha256": sha})
        (unpacked / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        # Validate all packages before promoting the staging directory.
        suffix = ".exe" if os.name == "nt" else ""
        for subdir, pattern in (("core", "Arduino.h"), ("core", "boards.txt"), ("avr-gcc", "avr-g++" + suffix), ("avrdude", "avrdude" + suffix), ("avrdude", "avrdude.conf")):
            find_one(unpacked / subdir, pattern)
        unpacked.rename(target)
    log(f"Toolchain AVR instalada em {target}")
    return installed_paths(store)
