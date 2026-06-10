import ctypes
import locale
import os
import subprocess
import sys

class CodeExecutor:
    def run_python_code(self, file_path):
        try:
            result = subprocess.run([sys.executable, file_path], capture_output=True, timeout=15)
            if result.returncode == 0:
                return True, self._decode_output(result.stdout)
            return False, self._decode_output(result.stderr)
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
