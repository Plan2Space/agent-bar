"""Trava da suíte sem rede (C1/F1): o guarda recusa `gh` de verdade e sockets para fora do loopback, e o fixture `gui`
da barra não sobe a coleta real. Sem nenhuma chamada ao GitHub."""
from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from tests import conftest as C
from tests.test_agent_bar import gui  # noqa: F401 (fixture)


def test_running_the_real_gh_is_refused_and_recorded():
    for argv in (["gh", "api", "user"], [r"C:\Program Files\GitHub CLI\gh.exe", "pr", "list"]):
        with pytest.raises(C.NetworkForbidden):
            subprocess.run(argv, capture_output=True)
    assert len(C.ATTEMPTS) == 2
    C.ATTEMPTS.clear()                                                             # esperado aqui: não reprova


def test_the_collector_s_run_gh_turns_the_refusal_into_a_failure_not_a_call():
    from agent_bar import collectors as col
    rc, out = col.run_gh(["api", "user"])
    assert rc == 127 and "rede proibida" in out
    C.ATTEMPTS.clear()


def test_a_socket_outside_loopback_is_refused_but_loopback_works():
    with pytest.raises(C.NetworkForbidden):
        socket.create_connection(("140.82.112.3", 443), timeout=1)               # api.github.com
    C.ATTEMPTS.clear()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname(), timeout=2)                  # loopback: permitido
    cli.close()
    srv.close()


def test_the_fake_gh_scripts_still_run():
    r = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
    assert r.stdout.strip() == "ok"


@pytest.mark.real_network
def test_a_marked_test_may_use_the_network():
    """Só para provar que o marcador desliga o guarda (fora da suíte padrão: não roda no CI)."""
    assert subprocess.Popen.__init__ is C._real_popen_init


def test_the_bar_fixture_never_starts_the_real_collection(gui):
    bar, _app, _tmp = gui
    assert not bar.worker.alive()                                                  # coleta nunca começou
    assert not C.ATTEMPTS



def test_a_test_that_tried_the_network_is_failed_at_teardown():
    with pytest.raises(AssertionError, match="tentou usar a rede"):
        C.check_attempts(["gh api user"])
    C.check_attempts([])                                                           # nada tentado: passa
