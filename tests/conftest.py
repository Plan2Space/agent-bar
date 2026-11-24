"""Testes sem rede (correção C1/F1 do relatório da cota REST, 2026-11-24).

A suíte gastava ~1.500 chamadas REST do GitHub por rodada: o fixture `gui` subia a coleta REAL. Daqui em diante,
nenhum teste da suíte padrão chama o GitHub nem abre conexão fora da máquina:

- executar o `gh` de verdade (`gh`/`gh.exe`, inclusive pelo caminho absoluto) é recusado com `NetworkForbidden`
  (um `OSError`, que o `run_gh` já transforma em falha) e a tentativa REPROVA o teste no fim;
- abrir socket para fora do loopback (127.0.0.1, ::1, localhost) é recusado do mesmo jeito.

O `gh` falso dos testes (`python tests/cockpit/fake_gh.py`) continua valendo: é o Python, não o `gh`. Teste que precisa
de rede de verdade se marca com `real_network`, `real_github` ou `real_agent` (todos fora da suíte padrão).
"""
from __future__ import annotations

import os
import socket
import subprocess

import pytest

REAL_MARKERS = ("real_network", "real_github", "real_agent")
LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


class NetworkForbidden(OSError):
    """Rede proibida em teste (sem marcador real_*)."""


ATTEMPTS: list[str] = []


def _is_gh(args) -> bool:
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    if not isinstance(first, (str, os.PathLike)):
        return False
    name = os.path.basename(os.fspath(first)).lower().split()[0] if os.fspath(first).strip() else ""
    return name in ("gh", "gh.exe")


_real_popen_init = subprocess.Popen.__init__
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _host(address) -> str:
    return str(address[0]) if isinstance(address, tuple) and address else str(address)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    allowed = any(request.node.get_closest_marker(m) for m in REAL_MARKERS)
    if allowed:
        yield
        return
    ATTEMPTS.clear()

    def popen_init(self, args, *a, **kw):
        if _is_gh(args):
            ATTEMPTS.append(f"gh {' '.join(map(str, args[1:])) if isinstance(args, (list, tuple)) else args}")
            raise NetworkForbidden("rede proibida em teste: executar o gh de verdade")
        return _real_popen_init(self, args, *a, **kw)

    def guard(real):
        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and _host(address) not in LOOPBACK:
                ATTEMPTS.append(f"socket {address!r}")
                raise NetworkForbidden(f"rede proibida em teste: conexão a {address!r}")
            return real(self, address)
        return connect

    monkeypatch.setattr(subprocess.Popen, "__init__", popen_init)
    monkeypatch.setattr(socket.socket, "connect", guard(_real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(_real_connect_ex))
    yield
    tried, ATTEMPTS[:] = list(ATTEMPTS), []
    check_attempts(tried)


def check_attempts(tried: list[str]) -> None:
    """Reprova o teste que tentou usar a rede, mesmo que a recusa tenha sido engolida pelo código testado."""
    assert not tried, f"o teste tentou usar a rede (sem marcador real_*): {tried[:5]}"
