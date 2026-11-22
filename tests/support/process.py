"""process.py — utilitario de teste: um PID ainda roda? Independente de idioma/codepage do Windows.

`tasklist` imprime mensagens localizadas (ex.: "INFORMAÇÕES:" em cp850); decodifica-las como texto quebra com
PYTHONUTF8=1. Aqui a pergunta vai direto ao kernel (OpenProcess + GetExitCodeProcess), sem texto nenhum.
"""
from __future__ import annotations

import os
import sys

STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
