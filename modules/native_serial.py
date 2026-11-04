"""Small read-only serial monitor using OS APIs, without Arduino CLI/PIO/pyserial."""
from __future__ import annotations

import codecs
import glob
import os
import re
import sys


def ports():
    if os.name == "nt":
        import winreg
        result = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    result.append(str(winreg.EnumValue(key, index)[1]))
        except FileNotFoundError:
            pass
        return sorted(set(result))
    return sorted({p for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/cu.*") for p in glob.glob(pattern)})


def validate_port(port, baud):
    if not isinstance(port, str) or not port.strip():
        raise ValueError("Informe a porta serial.")
    if not isinstance(baud, int) or baud < 300 or baud > 2_000_000:
        raise ValueError("Baud deve ser inteiro de 300 a 2000000.")
    if os.name == "nt" and not re.fullmatch(r"COM[1-9][0-9]*", port, re.I):
        raise ValueError("Porta Windows deve ser COM seguida de número.")
    if os.name != "nt" and not port.startswith("/dev/"):
        raise ValueError("Porta serial deve estar em /dev/.")


def monitor(port, baud=115200):
    validate_port(port, baud)
    print(f"Monitor nativo: {port}, {baud} baud, 8N1. Interrompa o processo para encerrar.", flush=True)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    def emit(chunk):
        sys.stdout.write(decoder.decode(chunk))
        sys.stdout.flush()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        class DCB(ctypes.Structure):
            _fields_ = [("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("wReserved", wintypes.WORD), ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
                        ("ByteSize", ctypes.c_ubyte), ("Parity", ctypes.c_ubyte), ("StopBits", ctypes.c_ubyte),
                        ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                        ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", wintypes.WORD)]
        class Timeouts(ctypes.Structure):
            _fields_ = [(name, wintypes.DWORD) for name in ("interval", "read_multiplier", "read_constant", "write_multiplier", "write_constant")]
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
        kernel.SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
        kernel.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(Timeouts)]
        kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW("\\\\.\\" + port, 0xC0000000, 0, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            if not kernel.GetCommState(handle, ctypes.byref(dcb)):
                raise ctypes.WinError(ctypes.get_last_error())
            dcb.BaudRate, dcb.ByteSize, dcb.Parity, dcb.StopBits = baud, 8, 0, 0
            dcb.flags = 1 | (1 << 4) | (1 << 12)  # binary, DTR/RTS enabled; no flow control
            if not kernel.SetCommState(handle, ctypes.byref(dcb)):
                raise ctypes.WinError(ctypes.get_last_error())
            timeouts = Timeouts(50, 0, 100, 0, 100)
            if not kernel.SetCommTimeouts(handle, ctypes.byref(timeouts)):
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(4096)
            while True:
                count = wintypes.DWORD()
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if count.value:
                    emit(buffer.raw[:count.value])
        finally:
            kernel.CloseHandle(handle)
    else:
        import select
        import termios
        import tty
        speed = getattr(termios, "B" + str(baud), None)
        if speed is None:
            raise ValueError("Baud não suportado pela API serial deste sistema.")
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        original = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = attrs[5] = speed
            attrs[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            while True:
                if select.select([fd], [], [], .1)[0]:
                    data = os.read(fd, 4096)
                    if not data:
                        raise OSError("Dispositivo serial desconectado.")
                    emit(data)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, original)
            os.close(fd)
