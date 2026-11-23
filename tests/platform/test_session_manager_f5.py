"""F5 completo (SPEC de UX): isolamento por workspace, heartbeat (processo morto, turno parado, sessao ociosa) e
orfaos — so processos que o proprio Studio iniciou, conferidos por PID + instante de criacao."""
from __future__ import annotations

import subprocess
import sys
import time

from agent_bar.platform.adapter import SessionRequest
from agent_bar.platform.proc import creation_time, same_process
from agent_bar.platform.sessions import SessionManager
from tests.platform.test_session_manager import Fake, _cleanup  # noqa: F401 - fixture autouse
from tests.support.process import pid_alive


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def by_workspace():
    made = []

    def factory(ws):
        a = Fake()
        a.workspace = ws
        made.append(a)
        return a
    return made, factory


def test_each_workspace_gets_its_own_agent_process_and_sessions_never_cross():
    made, factory = by_workspace()
    with SessionManager({"a": factory}) as m:
        s1 = m.open("a", SessionRequest(workspace="C:/ws1"))
        s2 = m.open("a", SessionRequest(workspace="C:/ws2"))
        s3 = m.open("a", SessionRequest(workspace="C:/ws1"))
        assert [a.workspace for a in made] == ["C:/ws1", "C:/ws2"]           # ws1 reaproveita o seu processo
        assert set(made[0].get_status().sessions) == {s1["session"]["session_id"], s3["session"]["session_id"]}
        assert set(made[1].get_status().sessions) == {s2["session"]["session_id"]}
        made[1].crash()
        report = m.heartbeat()
        assert report["down"] == [s2["id"]]                                   # so o workspace que caiu
        assert m.get(s1["id"])["state"] == "READY" and m.get(s2["id"])["state"] == "ERROR"


def test_the_limit_is_per_provider_across_workspaces():
    made, factory = by_workspace()
    with SessionManager({"a": factory}, limits={"a": 2}) as m:
        m.open("a", SessionRequest(workspace="C:/ws1"))
        m.open("a", SessionRequest(workspace="C:/ws2"))
        import pytest

        from agent_bar.platform.sessions import SessionLimitReached
        with pytest.raises(SessionLimitReached):
            m.open("a", SessionRequest(workspace="C:/ws3"))


def test_a_turn_without_events_is_flagged_stalled_and_clears_when_events_resume():
    clock = Clock()
    with SessionManager({"a": lambda: Fake()}, clock=clock, stall_timeout=60) as m:
        s = m.open("a")
        fut = m.send(s["id"], "conte devagar")
        time.sleep(0.2)
        clock.t += 61
        assert m.heartbeat()["stalled"] == [s["id"]] and m.get(s["id"])["stalled"] is True
        assert m.heartbeat()["stalled"] == []                                 # avisa uma vez
        time.sleep(0.2)                                                       # chegaram eventos novos
        assert m.get(s["id"])["stalled"] is False
        m.cancel(s["id"])
        assert fut.result(5).stop_reason == "cancelled"


def test_an_idle_session_is_closed_to_free_the_process_and_can_be_recovered():
    clock = Clock()
    with SessionManager({"a": lambda: Fake()}, clock=clock, idle_timeout=600) as m:
        s = m.open("a")
        busy = m.open("a")
        pids = Fake.instances[0].owned_pids()
        clock.t += 601
        m.send(busy["id"], "conte devagar")                                   # em uso: nao fecha
        report = m.heartbeat()
        assert report["idle_closed"] == [s["id"]]
        got = m.get(s["id"])
        assert got["state"] == "CLOSED" and "ociosidade" in got["error"] and all(pid_alive(p) for p in pids)
        m.cancel(busy["id"])
        assert m.recover(s["id"])["state"] == "READY"


def test_processes_left_by_a_crashed_studio_are_found_and_only_those_are_ended(tmp_path):
    m1 = SessionManager({"a": lambda: Fake()}, store_path=tmp_path / "s.json")
    m1.open("a")
    (pid,) = Fake.instances[0].owned_pids()
    m1._save()                                                                # o Studio "cai" aqui, sem fechar nada
    ended = []
    m2 = SessionManager({"a": lambda: Fake()}, store_path=tmp_path / "s.json", killer=lambda p: ended.append(p) or
                        subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True).returncode == 0)
    assert [o["pid"] for o in m2.orphans] == [pid]
    assert m2.end_orphans() == [pid] and ended == [pid]
    end = time.monotonic() + 5
    while time.monotonic() < end and pid_alive(pid):
        time.sleep(0.05)
    assert not pid_alive(pid) and m2.orphans == []


def test_a_reused_pid_is_never_treated_as_an_orphan(tmp_path):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])    # processo que NAO e do Studio
    try:
        (tmp_path / "s.json").write_text(
            '{"schema": "p2s.session-manager.v1", "sessions": [], "processes": '
            f'[{{"provider_id": "a", "workspace": "", "pid": {other.pid}, "created": {creation_time(other.pid) - 5}}}]}}',
            encoding="utf-8")
        killed = []
        m = SessionManager({"a": lambda: Fake()}, store_path=tmp_path / "s.json", killer=killed.append)
        assert m.orphans == [] and m.end_orphans() == [] and killed == []
        assert pid_alive(other.pid)
    finally:
        other.kill()
        other.wait(10)


def test_process_identity_helpers():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        created = creation_time(p.pid)
        assert created and same_process(p.pid, created) and not same_process(p.pid, created + 1)
    finally:
        p.kill()
        p.wait(10)
    assert creation_time(p.pid) is None and not same_process(p.pid, created)
