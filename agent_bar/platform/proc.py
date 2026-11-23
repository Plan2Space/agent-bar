"""proc.py — identidade de processo para achar orfaos sem matar processo alheio.

Um PID sozinho nao identifica um processo: o Windows reaproveita PIDs. A identidade aqui e (PID, instante de criacao):
so e "o mesmo processo" se os dois baterem. O Session Manager grava essa identidade dos processos que iniciou; numa
abertura seguinte, so encerra os que ainda batem (orfaos dele), nunca um processo que herdou o PID.
"""
from __future__ import annotations

import os
import subprocess
import sys


def creation_time(pid: int) -> float | None:
    """Instante de criacao do processo (FILETIME em segundos) ou None se ele nao existe/nao da para ler."""
    if sys.platform != "win32":
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
                return float(fh.read().rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            return None
    import ctypes
    from ctypes import wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wt.HANDLE
    h = k32.OpenProcess(0x1000, False, pid)                                   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        times = [wt.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        code = wt.DWORD()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return None
        ft = times[0]
        return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
    finally:
        k32.CloseHandle(h)


def same_process(pid: int, created: float | None) -> bool:
    now = creation_time(pid)
    return now is not None and created is not None and abs(now - created) < 0.01


def kill_tree(pid: int) -> bool:
    """Encerra o processo e os filhos dele. So chamar para um processo cuja identidade foi conferida."""
    if sys.platform == "win32":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return r.returncode == 0
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False
