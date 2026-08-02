import ctypes
import locale
import ctypes
import os
import subprocess
import sys

class CodeExecutor:
    def run_python_code(self, file_path, on_start=None):
        try:
            process = subprocess.Popen(
                [sys.executable, file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if on_start:
                on_start(process)
            output, _ = process.communicate()
            decoded = self._decode_output(output)
            return process.returncode == 0, decoded
        except Exception as e:
            return False, str(e)

    def _decode_output(self, output):
        if isinstance(output, str):
            return output
        if not output:
            return ""

        encodings = []
        if os.name == "nt":
            encodings.append("utf-8-sig")
            try:
                encodings.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
            except Exception:
                pass
            encodings.extend(["mbcs", "cp1252"])
        else:
            encodings.extend([locale.getpreferredencoding(False), "utf-8"])

        for encoding in dict.fromkeys(encodings):
            try:
                return output.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return output.decode("utf-8", errors="replace")
