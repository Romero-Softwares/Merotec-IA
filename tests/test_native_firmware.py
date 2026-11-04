import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from modules import native_firmware as firmware
from modules import firmware_packages as packages
from modules.electronics import tool_arguments


class NativeFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_native_commands_never_call_cli_or_pio(self):
        for action in ("install", "boards", "build", "upload", "monitor"):
            args = tool_arguments("native", action, port="COM3")
            self.assertTrue(args[2].endswith("native_firmware.py"))
            self.assertNotIn("arduino-cli", args)
            self.assertNotIn("pio", args)
        with self.assertRaises(ValueError):
            tool_arguments("native", "upload")
        with self.assertRaises(ValueError):
            tool_arguments("native", "monitor", port="COM3", baud="115200.5")

    def test_manifest_and_fqbn_configuration(self):
        self.assertEqual("uno", firmware.configuration(self.root, "arduino:avr:uno")["board"])
        self.write("merotec-firmware.json", json.dumps({"version": 1, "board": "nano-old", "source_dir": "."}))
        self.assertEqual("nano-old", firmware.configuration(self.root)["board"])
        self.write("merotec-firmware.json", '{"version":1,"board":"uno","source_dir":"../"}')
        with self.assertRaises(ValueError):
            firmware.configuration(self.root)

    def test_native_pio_environment_selection_and_unsupported_platform(self):
        (self.root / "src").mkdir()
        self.write("platformio.ini", "[env:uno]\nplatform=atmelavr\nframework=arduino\nboard=uno\n[env:nano]\nplatform=atmelavr\nframework=arduino\nboard=nanoatmega328\n")
        with self.assertRaises(ValueError):
            firmware.configuration(self.root)
        self.assertEqual("nano-old", firmware.configuration(self.root, environment="nano")["board"])
        self.write("platformio.ini", "[env:esp32]\nplatform=espressif32\nframework=arduino\nboard=esp32dev\n")
        with self.assertRaisesRegex(ValueError, "ESP32/STM32"):
            firmware.configuration(self.root)

    def test_libraries_are_discovered_transitively(self):
        self.write("libraries/First/src/First.h", "#include <Second.h>\n")
        self.write("libraries/Second/src/Second.h", "// second\n")
        self.write("libraries/Unused/src/Unused.h", "// unused\n")
        chosen = firmware.discover_libraries(["#include <First.h>"], [self.root / "libraries"])
        self.assertEqual(["First", "Second"], [p.parent.name for p in chosen])

    def test_build_failure_prevents_upload(self):
        with patch.object(firmware, "build", side_effect=RuntimeError("compile failed")), patch.object(firmware, "run") as run:
            with self.assertRaises(RuntimeError):
                firmware.upload(self.root, "COM3")
            run.assert_not_called()
        with patch.object(firmware, "build") as build:
            with self.assertRaises(ValueError):
                firmware.upload(self.root, "")
            build.assert_not_called()

    def test_pipeline_builds_current_sources_and_checks_memory(self):
        self.write("blink.ino", "void setup() {}\nvoid loop() {}\n")
        self.write("core/Arduino.h", "// header\n")
        self.write("core/main.cpp", "int main() {}\n")
        self.write("platform/variants/standard/pins_arduino.h", "// pins\n")
        suffix = ".exe" if os.name == "nt" else ""
        for name in ("avr-g++", "avr-gcc", "avr-gcc-ar", "avr-objcopy", "avr-size"):
            self.write("bin/" + name + suffix, "fixture")
        tools = {"core": self.root / "core", "platform": self.root / "platform", "bin": self.root / "bin"}
        # Root fixture core isn't a sketch source folder; use explicit src manifest.
        self.write("src/main.cpp", "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n")
        self.write("merotec-firmware.json", '{"version":1,"board":"uno","source_dir":"src"}')
        with patch.object(firmware, "installed_paths", return_value=tools), patch.object(firmware, "run") as run, patch.object(firmware.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, ".text 100 0\n.data 10 0\n.bss 20 0\n")):
            report = firmware.build(self.root, log=lambda text: None)
            self.assertEqual(110, report["flash_bytes"])
            self.assertEqual(30, report["static_ram_bytes"])
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(Path(command[0]).stem == "avr-objcopy" for command in commands))
            self.assertFalse(any("arduino-cli" in str(command) or "platformio" in str(command) for command in commands))
        with patch.object(firmware, "installed_paths", return_value=tools), patch.object(firmware, "run"), patch.object(firmware.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, ".text 40000 0\n")):
            with self.assertRaisesRegex(ValueError, "excede memória"):
                firmware.build(self.root, log=lambda text: None)


class FirmwarePackageTests(unittest.TestCase):
    def test_serial_port_and_baud_validation(self):
        from modules.native_serial import validate_port
        validate_port("COM3" if os.name == "nt" else "/dev/ttyUSB0", 115200)
        for value in (0, -1, 115200.5, 3000000):
            with self.assertRaises(ValueError):
                validate_port("COM3" if os.name == "nt" else "/dev/ttyUSB0", value)
        with self.assertRaises(ValueError):
            validate_port("", 115200)

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                packages.extract(archive, Path(folder) / "unpacked")
            self.assertFalse((Path(folder) / "escape.txt").exists())

    def test_download_verifies_checksum(self):
        class Response(io.BytesIO):
            url = "https://example.test/file"
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "archive"
            checksum = "SHA-256:" + hashlib.sha256(b"package").hexdigest()
            with patch.object(packages.urllib.request, "urlopen", return_value=Response(b"package")):
                packages.download(Response.url, target, checksum)
            with patch.object(packages.urllib.request, "urlopen", return_value=Response(b"bad")):
                with self.assertRaisesRegex(ValueError, "Checksum"):
                    packages.download(Response.url, target, checksum)
